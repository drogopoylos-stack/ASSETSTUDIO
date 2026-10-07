"""Deterministic visual review — show an agent the frame it is actually judging.

An agent asked to review a spell, a character or a UI screen reaches for a headless browser and
takes a screenshot. That screenshot is wrong four times out of five, and the reasons are always the
same four:

  time    a spell peaks for ~200ms; one frame at an arbitrary moment misses the peak
  state   a page load shows the menu, not the pose, the cast, or the boss
  scale   a 40px effect inside a 1280x720 frame is not visible to the reviewer at all
  chance  seeds and physics differ per run, so no two reviews compare and no A/B is possible

None of that is fixed by a better screenshot tool, so this module does not provide one. It provides
a driven clock. `performance.now`, `Date`, `requestAnimationFrame` and `Math.random` are replaced
BEFORE any page script runs, and the frame loop is then stepped by hand. Ask for t=0, 0.25, 0.5 and
you get exactly those frames, the same bytes every run, on any engine that draws through
requestAnimationFrame — which is 2D canvas, Three.js, PixiJS, Phaser and hand-rolled loops alike.
The engine needs no cooperation and no plugin; it never learns the clock is not real.

Two modes, because "is this asset good" and "does it read in play" are different questions:

  isolate   mount ONE thing on a known backdrop from the project's `review/targets.js`
  scene     load the real game and drive a scripted input sequence against the stepped clock

Scene mode needs no per-project file, so every workspace can use it the minute it is opened.

The output is one contact sheet per review, not N screenshots: a labelled grid of the frames plus
the same row again at the size the player actually sees it. That second row is where "the spell is
invisible during play" becomes obvious, and it is the judgement a 512px hero render can never make.
Numbers ride alongside — ink coverage, luminance, palette, silhouette, motion — because a reviewer
told "peak coverage 3% at t=0.4s" catches a dead effect without spending a single image token.

Renderer: the user's own Chrome, headless, driven over the DevTools protocol. No Playwright, no
browser download, no second Chromium — `websockets` and `httpx` are already here for the API, and
`workspace._find_chrome()` already knows where Chrome lives. One browser is kept alive across
reviews (launching is the cost, not the tab) and reaped when it goes idle.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

import httpx
import websockets

from .config import DATA_DIR, settings

_PORT_LO = 9330
_IDLE_KILL = 10 * 60.0        # a browser nobody has reviewed with for 10 min is 300MB of nothing


def _idle_kill() -> float:
    """Seconds of no review before the browser is closed. Settings → Studio engine sets it in minutes."""
    try:
        m = float((settings.get("engine") or {}).get("browser_idle_min") or 0)
    except Exception:
        m = 0.0
    return m * 60.0 if m > 0 else _IDLE_KILL
_REAP_TICK = 60.0
_MAX_PARALLEL = 2             # tabs at once; more just thrashes one GPU
_NAV_SETTLE = 0.8             # seconds of real time for the page to fetch and parse
# Sheet sizing. A vision model resizes an image down to its own working resolution, so pixels
# beyond that are billed and then discarded — but pixels BELOW it are detail the reviewer simply
# never gets. A 300px cell downscales a 720px capture by 2.4x into a 1250px sheet: half the
# resolution the model can use, so it pays for a small image AND throws the craft away. Rule:
# never shrink a frame below 1:1 unless the whole sheet would exceed the budget, and spend that
# budget on fewer, larger cells rather than more, smaller ones.
# LONG EDGE OF THE FINISHED SHEET, PX. This is not a taste: a vision model keeps an image
# 1:1 up to a ~1568px long edge and resizes anything longer before it ever looks. At 2400
# every sheet was quietly scaled to 0.65, so a third of every pixel we rendered was thrown
# away — we paid to render it, the model never saw it, and the subject inside got smaller
# for nothing. Render at the size that survives.
_SHEET_MAX = 1568
# Named presets, because "how big should the sheet be" is a judgement about what the review is
# FOR. `draft` answers "does the arc work" for a few hundred tokens; `high` is for judging craft.
# N reviewers share one sheet, and a shared sheet sits in the cached prompt prefix, so the honest
# default is the one that can actually see the art.
# (pixel budget, capture scale). The budget is an AREA, not a long edge: a vision model bills an
# image by its pixel count, so a tall sheet and a wide one of the same area cost the same. Capping
# the long edge instead punished portrait layouts for no reason and let a 2-column sheet quietly
# cost twice a 4-column one. Roughly: draft ~800 tokens, normal ~2200, high ~4300.
# The budget is the AREA of the whole sheet, and every preset now sits under the ~1.15Mpx a
# model keeps unresized. `high` therefore does not buy a bigger canvas — a bigger one only
# gets shrunk on arrival. It buys a sharper one: capture at 3x and downsample, so the
# panel the model reads is supersampled instead of merely larger.
QUALITY = {"draft": (600_000, 1), "normal": (1_120_000, 2), "high": (1_120_000, 3)}
# A cell is never upscaled past its frame, and the pixel budget already bounds the sheet, so
# this only ever needs to stop a single frame from producing an absurd canvas. It used to be 700,
# which capped even a one-frame sheet at roughly half native and made interface text unreadable
# — the sheet shrank the frame, then the vision model shrank the sheet again. A model keeps a
# picture 1:1 up to a ~1568px long edge, and one 1280x800 frame fits inside that comfortably.
_CELL_MAX = 1600
_PLAY_H = 96                  # the "as the player sees it" strip height, px
_SCALE = 2                    # capture at 2x device pixels, present at 1x — real antialiasing

_lock = threading.Lock()
_slots = threading.Semaphore(_MAX_PARALLEL)
_state: dict = {"proc": None, "ws": "", "port": 0, "profile": "", "seen": 0.0, "gpu": None,
                "started": 0.0, "who": None}
# `who` is what the browser is CURRENTLY for: the project, the label the agent gave the
# review, and when. Without it the status pill can only say that something is running, and
# "a browser is open somewhere, for some reason" is not information anyone can act on.
# Warm tabs, keyed by whatever the caller calls a session. Reloading a game costs 2-3s of
# warm-up, which is the whole cost of a review once the browser is up; keeping the page alive and
# firing the effect again turns an iterate-on-one-fireball loop from seconds into well under one.
# Only the target id is kept -- the websocket cannot outlive its event loop, but the tab can, and
# reattaching to a live target preserves the page exactly as it was left.
_tabs: dict = {}
_TAB_IDLE = 15 * 60.0
# THE TABS A RENDER IS USING RIGHT NOW. Two agents can review at the same moment, and the sweep
# below closes any page the Studio does not recognise — so a page that IS being rendered into has
# to be recognisable while it exists, not only after its `finally` puts it in `_tabs`.
_inflight: set = set()
_reaper_started = False


# ---------------------------------------------------------------------------
# The injected runtime
# ---------------------------------------------------------------------------
# Runs before every page script, on every frame and every iframe. Everything here exists to make
# the page render a FUNCTION of time rather than of when the screenshot happened to land.
SHIM = r"""
(() => {
  const EPOCH = 1700000000000;
  let now = 0, seed0 = 1234567 >>> 0, seed = seed0, rafId = 1;
  const rafs = new Map();
  const RealDate = Date;

  performance.now = () => now;
  class FakeDate extends RealDate {              // subclass, not Proxy, so instanceof survives
    constructor(...a) { if (a.length === 0) super(EPOCH + now); else super(...a); }
    static now() { return EPOCH + now; }
  }
  window.Date = FakeDate;

  const mulberry = () => {
    seed = (seed + 0x6D2B79F5) >>> 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  Math.random = mulberry;

  window.requestAnimationFrame = (fn) => { const id = rafId++; rafs.set(id, fn); return id; };
  window.cancelAnimationFrame = (id) => { rafs.delete(id); };

  // WebGL discards its drawing buffer after compositing unless asked not to, so a capture taken
  // after the frame returns solid black. Every 3D review depends on this one line.
  const realGetContext = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (type, attrs) {
    if (String(type).indexOf('webgl') === 0)
      attrs = Object.assign({}, attrs, { preserveDrawingBuffer: true });
    return realGetContext.call(this, type, attrs);
  };

  // What the browser composites the canvas onto. A 3D canvas is usually cleared to
  // transparent or to black, and the page behind it supplies the backdrop the player sees.
  // Reading it here beats sampling a corner pixel, which is part of the picture being judged.
  function backdrop(c) {
    const opaque = (s) => {
      const m = String(s || '').match(/rgba?\(([^)]+)\)/);
      if (!m) return null;
      const p = m[1].split(',').map((x) => parseFloat(x));
      if (p.length > 3 && p[3] < 0.99) return null;
      return [p[0] | 0, p[1] | 0, p[2] | 0];
    };
    let el = c || document.body;
    while (el) {
      const v = opaque(getComputedStyle(el).backgroundColor);
      if (v) return v;
      el = el.parentElement;
    }
    return [0, 0, 0];
  }

  // How much of the screen is interface drawn in HTML rather than into the canvas.
  // A pure canvas game (Three.js, PlayCanvas) answers ~0 and the canvas IS the picture. A game
  // that paints a map into a canvas and lays its HUD, panels and menus over it in HTML answers
  // high — and grabbing the canvas there shows the map with the whole interface missing, which
  // is a blind review of the part the developer is actually working on.
  function domShare(c) {
    if (!c) return 1;
    let other = 0, seen = 0;
    for (let y = 0.06; y < 1; y += 0.11) {
      for (let x = 0.05; x < 1; x += 0.1) {
        const el = document.elementFromPoint(Math.round(x * innerWidth),
                                             Math.round(y * innerHeight));
        if (!el) continue;
        seen++;
        if (el !== c && !c.contains(el) && el !== document.documentElement) other++;
      }
    }
    return seen ? other / seen : 0;
  }

  function canvases() {
    const out = [];
    for (const c of document.querySelectorAll('canvas'))
      if (c.width > 8 && c.height > 8) out.push(c);
    out.sort((a, b) => b.width * b.height - a.width * a.height);
    return out;
  }

  // A stylesheet or script that fails to load changes everything about how a page looks, and
  // it is invisible in the picture itself -- the page just renders wrong. IMPERATOR ROME linked
  // a `css/ui.css` that was never written, and every review of it was a review of unstyled HTML
  // without anyone knowing. Catching it costs one listener.
  const missed = [];
  addEventListener('error', (e) => {
    const t = e.target;
    if (t && (t.src || t.href) && missed.length < 12) missed.push(String(t.src || t.href));
  }, true);

  window.__review = {
    ready: true,
    misses: () => missed.slice(),
    now: () => now,
    seed(s) { seed0 = s >>> 0; seed = seed0; },

    // Advance the clock and run whatever the page queued for the next frame. A callback that
    // throws must not stop the others -- a broken effect should show up as a bad frame in the
    // sheet, not as a hung review with no output at all.
    step(ms) {
      now += ms;
      // A game that already has a pause and a step hook knows its own update order, and covers
      // timers this shim deliberately does not fake (setTimeout/setInterval loops). Prefer it,
      // then still drain the frame queue so anything drawing through rAF keeps up.
      const ext = window.__reviewStep;
      if (typeof ext === 'function') {
        try { ext(ms); } catch (e) { (window.__review.errors ||= []).push('step: ' + e); }
      }
      const due = Array.from(rafs.values());
      rafs.clear();
      for (const fn of due) { try { fn(now); } catch (e) { (window.__review.errors ||= []).push(String(e)); } }
      return rafs.size;
    },

    // Readiness. Guessing with a fixed wait photographs a loading bar whenever the guess is
    // short. A game that sets window.__reviewReady = true (or a function returning true) when
    // its assets are in and the first real frame is drawable removes the guess entirely.
    // null means the game never opted in, and the caller falls back to the fixed warm-up.
    gameReady() {
      const r = window.__reviewReady;
      if (r === undefined) return null;
      if (typeof r === 'function') { try { return !!r(); } catch (e) { return false; } }
      return !!r;
    },

    // Cheap in-page probe. A full frame costs ~9ms to read back over CDP; measuring inside the
    // page costs a fraction of a millisecond because no image crosses the wire. So instead of
    // guessing which of six fixed times the explosion lands in, sample the whole window densely,
    // find the busy moment, and spend the expensive captures there.
    //
    // Activity is measured against a reference taken at probe start -- the resting scene. That
    // is the same idea as a clean plate: what matters is what CHANGED, not what is on screen.
    probe(dt, n) {
      const c = canvases()[0];
      if (!c) return null;
      const W = 96, H = Math.max(1, Math.round(W * c.height / c.width));
      const off = document.createElement('canvas');
      off.width = W; off.height = H;
      const g = off.getContext('2d', { willReadFrequently: true });
      const snap = () => { g.drawImage(c, 0, 0, W, H); return g.getImageData(0, 0, W, H).data; };
      const ref = snap();
      const out = [];
      const t0 = now;                 // sample times are relative to the post-warm-up reload
      let prev = ref;
      for (let i = 0; i < n; i++) {
        this.step(dt);
        const px = snap();
        let changed = 0, moved = 0;
        for (let p = 0; p < px.length; p += 4) {
          const dr = Math.abs(px[p] - ref[p]) + Math.abs(px[p + 1] - ref[p + 1]) +
                     Math.abs(px[p + 2] - ref[p + 2]);
          if (dr > 24) changed++;
          moved += Math.abs(px[p] - prev[p]);
        }
        prev = px;
        out.push({ t: now - t0, ink: changed / (W * H), motion: moved / (W * H) / 255 });
      }
      return out;
    },

    // Isolate mode: throw away whatever the origin's index page started, so the target mounts
    // onto a clean document. Pending frame callbacks go too, or the game keeps animating
    // underneath the thing being reviewed.
    reset(bg, w, h) {
      rafs.clear();
      document.documentElement.style.margin = '0';
      document.body.innerHTML = '';
      document.body.style.cssText =
        'margin:0;width:100vw;height:100vh;display:grid;place-items:center;background:' + bg;
      const root = document.createElement('div');
      root.id = '__review_root';
      root.style.cssText = 'width:' + w + 'px;height:' + h + 'px;position:relative;overflow:hidden';
      document.body.appendChild(root);
      seed = seed0; now = 0;
      return true;
    },

    async mount(file, name, bg, w, h) {
      this.reset(bg, w, h);
      const mod = await import(file);
      const list = mod.targets || mod.default || [];
      const t = name ? list.find((x) => x && x.name === name) : list[0];
      if (!t) return { ok: false, error: 'no target ' + JSON.stringify(name),
                       available: list.map((x) => x && x.name) };
      const root = document.getElementById('__review_root');
      await t.mount(root, { width: w, height: h, background: bg, random: mulberry,
                            pixelRatio: window.devicePixelRatio || 1 });
      return { ok: true, name: t.name || name };
    },

    // Named actions the game registers on window.__review.actions. An engine with a heavy
    // runtime -- PlayCanvas, Babylon, Unity -- cannot be rebuilt cheaply in an isolate harness,
    // and rebuilding it would review the asset in a vacuum anyway: no game camera, no
    // post-processing, no real lighting. Firing the effect where it actually lives is both
    // cheaper and more faithful.
    async act(name, args) {
      const reg = (window.__review.actions) || {};
      const a = reg[name];
      if (!a) return { ok: false, error: 'no action ' + JSON.stringify(name),
                       available: Object.keys(reg) };
      const fn = (typeof a === 'function') ? a : a.run;
      if (typeof fn !== 'function') return { ok: false, error: 'action ' + name + ' has no run()' };
      try { await fn(args || {}); return { ok: true, name: name }; }
      catch (e) { return { ok: false, error: String(e) }; }
    },

    actionList() {
      const reg = (window.__review.actions) || {};
      return Object.keys(reg).map((k) => ({ name: k, note: (reg[k] && reg[k].note) || '' }));
    },

    async list(file) {
      const mod = await import(file);
      const list = mod.targets || mod.default || [];
      return list.map((x) => ({ name: x && x.name, note: (x && x.note) || '' }));
    },

    // Prefer the canvas: it is the real framebuffer, needs no compositor round trip, and keeps
    // its own resolution. Only DOM/CSS work falls through to a screenshot.
    grab() {
      const c = canvases()[0];
      if (!c) return null;
      // Anything meaningful on top of the canvas and the canvas alone is the wrong picture;
      // returning null sends the caller to a page photograph, which composites HTML and canvas
      // exactly as the player sees them.
      if (domShare(c) > 0.1) return null;
      // A blank canvas is not the picture. A game whose interface is HTML can keep an empty
      // canvas layer behind it; grabbing that hands back a black frame and the reviewer is
      // blind, while every number says the frame is full. Sample it first, and if nothing is
      // drawn return null so the caller photographs the page instead, which composites the
      // HTML and the canvas together the way the player sees them.
      try {
        const W = 64, H = Math.max(1, Math.round(W * c.height / c.width));
        const off = document.createElement('canvas');
        off.width = W; off.height = H;
        const g = off.getContext('2d', { willReadFrequently: true });
        g.drawImage(c, 0, 0, W, H);
        const px = g.getImageData(0, 0, W, H).data;
        let lo = 255, hi = 0, seen = 0;
        for (let p = 0; p < px.length; p += 4) {
          if (px[p + 3] === 0) continue;               // transparent: nothing painted here
          seen++;
          const v = (px[p] + px[p + 1] + px[p + 2]) / 3;
          if (v < lo) lo = v;
          if (v > hi) hi = v;
        }
        if (!seen || hi - lo < 2) return null;         // fully clear, or one flat colour
      } catch (e) { /* unreadable: fall through and try the capture anyway */ }
      try { return c.toDataURL('image/png'); } catch (e) { return null; }
    },

    shape() {
      const c = canvases()[0];
      const r = document.getElementById('__review_root');
      const box = (r || document.body).getBoundingClientRect();
      return { canvas: !!c, w: c ? c.width : Math.round(box.width),
               h: c ? c.height : Math.round(box.height), count: canvases().length,
               backdrop: backdrop(c), dom_share: Math.round(domShare(c) * 100) / 100 };
    },
  };
})();
"""


# ---------------------------------------------------------------------------
# Browser lifecycle
# ---------------------------------------------------------------------------
def available() -> tuple[bool, str]:
    """Can this machine render a review at all?"""
    try:
        from .workspace import _find_chrome
        exe = _find_chrome()
    except Exception as e:                       # pragma: no cover - import guard
        return False, f"could not look for Chrome: {e}"
    if not exe:
        # On a PC with neither Chrome nor Edge the Studio fetches a browser itself; say so, with the
        # progress, rather than "not found" while it is on its way.
        try:
            from . import browser_install
            st = browser_install.status()
            if st.get("installing"):
                return False, f"a headless browser is downloading for the forge and the review ({st.get('progress', 0)}%) — try again in a minute"
            if st.get("error"):
                return False, ("no Chrome or Edge, and the download failed: " + str(st["error"])
                               + ". Install Chrome, or set chrome_path in Settings.")
        except Exception:
            pass
        return False, ("Chrome was not found. Set it in Settings, or install Chrome — reviews "
                       "render in headless Chrome.")
    return True, ""


def _free_port() -> int:
    import socket
    for p in range(_PORT_LO, _PORT_LO + 40):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return 0


def _ensure_browser() -> str:
    """The debugger URL of a live headless Chrome, launching one if needed."""
    with _lock:
        gpu = bool(settings.get("cc_review_gpu", True))
        proc = _state["proc"]
        # Flags are fixed at launch, so a browser started under the other GPU setting has to go.
        if proc is not None and proc.poll() is None and _state["ws"] and _state["gpu"] == gpu:
            _state["seen"] = time.time()
            return _state["ws"]
        if proc is not None and _state["gpu"] != gpu:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                pass
            if _state.get("profile"):
                # NO LOCAL `import shutil` HERE. One inside this function makes the name local to
                # ALL of it, and the branch below — closing a browser that is being replaced —
                # then raised UnboundLocalError before any browser was launched. The module
                # imports shutil at the top; that is the one to use.
                shutil.rmtree(_state["profile"], ignore_errors=True)
            _state.update({"proc": None, "ws": "", "port": 0, "profile": ""})

        # Anything still alive at this point is being replaced, so it has to go with it. Falling
        # through to a fresh launch and simply overwriting _state abandons a running Chrome that
        # nothing will ever close again — four of them, holding 2.3 GB, is what that looked like.
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                pass
            if _state.get("profile"):
                shutil.rmtree(_state["profile"], ignore_errors=True)
            _state.update({"proc": None, "ws": "", "port": 0, "profile": ""})

        ok, why = available()
        if not ok:
            raise RuntimeError(why)
        from .workspace import _find_chrome
        exe = _find_chrome()
        port = _free_port()
        if not port:
            raise RuntimeError("no free port for the review browser")
        profile = tempfile.mkdtemp(prefix="studio-review-")
        args = [
            exe, "--headless=new", f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--disable-background-networking", "--mute-audio",
            "--hide-scrollbars", "--disable-gpu-vsync",
        ]
        # Chrome already picks the real GPU here when one is usable — measured as ANGLE/NVIDIA,
        # with a 0.4ms draw and a 9ms frame read-back. `--enable-unsafe-swiftshader` below is a
        # PERMISSION to fall back to software, not an instruction to use it, and it is what keeps
        # a 3D review from silently returning black on a machine with no usable GPU.
        # Forcing software instead (--use-angle=swiftshader) measured 98ms per read-back, 11x
        # worse, so it is offered only for the one case that justifies it: keeping the GPU free
        # while a local model is resident.
        if gpu:
            args += ["--enable-unsafe-swiftshader"]
        else:
            args += ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
        args += ["about:blank"]
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ws = ""
        for _ in range(80):
            if proc.poll() is not None:
                raise RuntimeError("the review browser exited while starting")
            try:
                ws = httpx.get(f"http://127.0.0.1:{port}/json/version",
                               timeout=1.0).json()["webSocketDebuggerUrl"]
                break
            except Exception:
                time.sleep(0.25)
        if not ws:
            proc.terminate()
            raise RuntimeError("the review browser never opened its debugging port")
        _state.update({"proc": proc, "ws": ws, "port": port, "profile": profile,
                       "seen": time.time(), "gpu": gpu, "started": time.time()})
        # NOT "who": None. The caller records what the review is for BEFORE the browser is asked
        # for, so clearing it here erased the reason for the very review that just launched it —
        # the first review after every start would have shown "open, with nothing running".
        _start_reaper()
        return ws


def shutdown() -> bool:
    with _lock:
        proc = _state["proc"]
        profile = _state.get("profile") or ""
        _state.update({"proc": None, "ws": "", "port": 0, "profile": "", "who": None, "started": 0.0})
        _tabs.clear()          # every warm tab died with the browser that held it
    if proc is None:
        return False
    # The CHILDREN, not just the root. Chrome is a dozen processes — renderer, GPU, network,
    # utility — and on Windows they are in no job object, so terminating the root leaves every
    # one of them running with the profile still on disk. Measured after a run of test processes
    # that each called this: 11 orphaned browsers holding 7.7 GB. `kill()` already walked the
    # tree; this did not, and that was the whole difference.
    kids = []
    try:
        import psutil
        kids = psutil.Process(proc.pid).children(recursive=True)
    except Exception:
        pass
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        pass
    for c in kids:
        try:
            c.kill()
        except Exception:
            pass
    if profile:
        shutil.rmtree(profile, ignore_errors=True)
    return True


def _start_reaper() -> None:
    global _reaper_started
    if _reaper_started:
        return
    _reaper_started = True

    def loop() -> None:
        while True:
            time.sleep(_REAP_TICK)
            # The live-link tabs no agent has called for their limit, first. One agent at work
            # keeps the whole browser alive, and it used to keep every finished agent's game
            # running with it (see live.reap_idle_tabs).
            try:
                from . import live
                live.reap_idle_tabs()
            except Exception:                    # a keeper thread must never die
                pass
            try:
                if _state["proc"] is not None and time.time() - _state["seen"] > _idle_kill():
                    shutdown()
            except Exception:                    # a keeper thread must never die
                pass

    threading.Thread(target=loop, daemon=True, name="review-reaper").start()


def status() -> dict:
    ok, why = available()
    proc = _state["proc"]
    return {"ok": ok, "error": why, "running": bool(proc is not None and proc.poll() is None),
            "port": _state["port"], "gpu": bool(settings.get("cc_review_gpu", True)),
            "quality": str(settings.get("cc_review_quality") or "normal"),
            "idle_s": round(time.time() - _state["seen"], 1) if _state["seen"] else 0.0}


# ---------------------------------------------------------------------------
# Who is the browser for, and what else is running
# ---------------------------------------------------------------------------
# The status pill used to say only "headless browser active". That is a fact with nowhere to go:
# you cannot tell whose it is, whether it is stuck, or whether it is even ours. Everything below
# exists so the pill can answer "for what project, since when, and may I close it".

_PROFILE_PREFIX = "studio-review-"


def _note_use(project: str, spec: dict, project_id: str) -> None:
    """Remember what this review is for, so the pill can say it while the review runs."""
    prev = _state.get("who") or {}
    _state["who"] = {
        "project": project,
        "project_id": project_id,
        "label": str(spec.get("label") or ""),
        "mode": str(spec.get("mode") or "scene"),
        "subject": str(spec.get("target") or spec.get("action") or ""),
        "url": str(spec.get("url") or ""),
        "since": time.time(),
        "renders": int(prev.get("renders", 0)) + 1,
    }


def _cmdline(p) -> str:
    try:
        return " ".join(p.cmdline() or [])
    except Exception:
        return ""


def _profile_of(cmdline: str) -> str:
    m = re.search(r"--user-data-dir=(?:\"([^\"]+)\"|(\S+))", cmdline)
    return (m.group(1) or m.group(2)) if m else ""


def _headless_roots() -> list:
    """Every automation browser on this machine, one entry per browser (not per child process).

    A Chrome is a dozen processes; counting them all reports "12 browsers" for one. Only roots —
    processes whose parent is not itself part of the same browser — are returned.
    """
    try:
        import psutil
    except Exception:
        return []
    found, mine = {}, set()
    for p in psutil.process_iter(["pid", "ppid", "name", "create_time"]):
        try:
            name = (p.info.get("name") or "").lower()
            if not (name.startswith(("chrome", "chromium", "msedge")) or "headless" in name):
                continue
            # The command line costs a Windows API call per process, so ask for it only once the
            # NAME already says this might be a browser.
            cl = "" if "headless" in name else _cmdline(p)
            if "--headless" not in cl and "headless" not in name and "ms-playwright" not in cl:
                continue
            mine.add(p.info["pid"])
            found[p.info["pid"]] = (p, name, cl)
        except Exception:
            continue
    out = []
    for pid, (p, name, cl) in found.items():
        if p.info.get("ppid") in mine:
            continue                              # a child of another browser process
        cl = cl or _cmdline(p)
        prof = _profile_of(cl)
        try:
            rss = sum((c.memory_info().rss for c in [p] + p.children(recursive=True)), 0)
        except Exception:
            rss = 0
        out.append({
            "pid": pid,
            "name": p.info.get("name") or "",
            "age_s": round(max(0.0, time.time() - (p.info.get("create_time") or time.time())), 1),
            "profile": prof,
            "mb": round(rss / 1048576, 1),
            # A profile under our own prefix is ours by construction — nothing else creates one.
            "studio": _PROFILE_PREFIX in prof,
            "kind": ("Playwright" if "ms-playwright" in cl else
                     "Studio review" if _PROFILE_PREFIX in prof else "headless Chrome"),
        })
    out.sort(key=lambda b: -b["age_s"])
    return out


def browsers() -> dict:
    """The full picture behind the status pill: ours, and anything else headless on the machine."""
    proc = _state["proc"]
    live = proc is not None and proc.poll() is None
    ours = None
    if live:
        who = dict(_state.get("who") or {})
        idle = time.time() - (_state["seen"] or time.time())
        ours = {
            "pid": proc.pid,
            "port": _state["port"],
            "profile": _state["profile"],
            "gpu": bool(_state.get("gpu")),
            "age_s": round(max(0.0, time.time() - (_state.get("started") or time.time())), 1),
            "idle_s": round(max(0.0, idle), 1),
            "closes_in_s": round(max(0.0, _idle_kill() - idle), 1),
            "tabs": [{"session": k, "url": v.get("url", ""),
                      "idle_s": round(max(0.0, time.time() - v.get("seen", 0)), 1)}
                     for k, v in _tabs.items()],
            "who": who or None,
        }
    everything = _headless_roots()
    family = set()
    if live:
        try:
            import psutil
            family = {c.pid for c in psutil.Process(proc.pid).children(recursive=True)}
            family.add(proc.pid)
        except Exception:
            family = {proc.pid}
    return {
        "ours": ours,
        "strays": [b for b in everything if b["pid"] not in family],
        "idle_kill_s": _idle_kill(),
    }


def kill(pids: list) -> dict:
    """Kill named browsers. Only ever called with pids this module just reported."""
    try:
        import psutil
    except Exception:
        return {"ok": False, "error": "psutil is not available", "killed": 0}
    known = {b["pid"] for b in _headless_roots()}
    killed, freed = 0, []
    for pid in {int(p) for p in pids or []}:
        # Refuse anything that is not, right now, a headless browser root. A pid is reused by the
        # OS, and a stale one from a page left open for ten minutes could name anything at all.
        if pid not in known:
            continue
        try:
            proc = psutil.Process(pid)
            prof = _profile_of(_cmdline(proc))
            for c in proc.children(recursive=True):
                try:
                    c.kill()
                except Exception:
                    pass
            proc.kill()
            proc.wait(timeout=5)
            killed += 1
            if _PROFILE_PREFIX in prof:
                freed.append(prof)
        except Exception:
            continue
    for prof in freed:
        shutil.rmtree(prof, ignore_errors=True)
    # If we just killed our own, forget it rather than hand out a dead debugger URL.
    proc = _state["proc"]
    if proc is not None and proc.poll() is not None:
        with _lock:
            _state.update({"proc": None, "ws": "", "port": 0, "profile": "", "who": None})
            _tabs.clear()
    return {"ok": True, "killed": killed, "profiles_removed": len(freed)}


def sweep_leftovers() -> dict:
    """Clear what a previous backend left behind. Safe to call at boot, and only then.

    A crash — or a hard restart — leaves the review browser running and its profile directory on
    disk. Neither is ever reclaimed: the browser holds a few hundred MB for nothing, and 27 dead
    profiles adding up to 1.8 GB is what this actually looked like before it was swept.
    Only directories under our own prefix are touched, and only when no live browser is using one.
    """
    killed, removed, bytes_freed = 0, 0, 0
    live = {b["profile"] for b in _headless_roots() if b["profile"]}
    # Ours, if one is somehow already up, is not a leftover.
    mine = _state["proc"].pid if _state["proc"] is not None and _state["proc"].poll() is None else 0
    # 1. browsers wearing one of our profiles that we did not start (we have started none yet)
    for b in _headless_roots():
        if not b["studio"] or b["pid"] == mine:
            continue
        try:
            import psutil
            proc = psutil.Process(b["pid"])
            for c in proc.children(recursive=True):
                try:
                    c.kill()
                except Exception:
                    pass
            proc.kill()
            proc.wait(timeout=5)
            killed += 1
            live.discard(b["profile"])
        except Exception:
            continue
    # 2. the profile directories themselves, minus any a surviving browser still holds
    try:
        tmp = Path(tempfile.gettempdir())
        for d in tmp.glob(_PROFILE_PREFIX + "*"):
            if not d.is_dir() or str(d) in live:
                continue
            try:
                bytes_freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            except Exception:
                pass
            shutil.rmtree(d, ignore_errors=True)
            if not d.exists():
                removed += 1
    except Exception:
        pass
    if killed or removed:
        print(f"[review] swept {killed} leftover browser(s), {removed} profile(s), "
              f"{bytes_freed / 1048576:.0f} MB")
    return {"ok": True, "killed": killed, "profiles_removed": removed,
            "mb_freed": round(bytes_freed / 1048576, 1)}


# ---------------------------------------------------------------------------
# DevTools protocol
# ---------------------------------------------------------------------------
class _Cdp:
    """One request/response pair at a time — a review is a short serial script, not a firehose."""

    # Longer than any legitimate call and far shorter than forever. A render is seconds; a heap
    # query on a big page is single-digit seconds; nothing here should ever reach this.
    CALL_TIMEOUT = 45.0

    def __init__(self, ws):
        self.ws = ws
        self.n = 0

    async def call(self, method: str, params: Optional[dict] = None,
                   session: str = "", timeout: float = 0.0) -> dict:
        """One CDP command, with a DEADLINE.

        Without one this loop can wait forever, and it did: an evaluate with `awaitPromise` on a
        page whose promise never settles gets no reply, and `/api/live/open` never returned —
        taking the forge, the debugger and animation review with it. A browser that has stopped
        answering is a thing worth SAYING; forty-five seconds is far longer than any call here
        legitimately takes, so a wait that reaches it is news rather than impatience.
        """
        self.n += 1
        mine = self.n                             # captured, never re-read: `self.n` moves on
        msg: dict[str, Any] = {"id": mine, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        await self.ws.send(json.dumps(msg))
        end = time.monotonic() + (timeout if timeout > 0 else self.CALL_TIMEOUT)
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError(
                    "%s: the browser sent no reply in %ds. The page may be waiting on a promise "
                    "that never settles — check that the URL is really being served."
                    % (method, int(timeout if timeout > 0 else self.CALL_TIMEOUT)))
            try:
                raw = json.loads(await asyncio.wait_for(self.ws.recv(), left))
            except asyncio.TimeoutError:
                continue                          # the deadline above owns the verdict
            if raw.get("id") != mine:
                continue                          # an event, or another session's reply
            if "error" in raw:
                raise RuntimeError(f"{method}: {raw['error'].get('message', raw['error'])}")
            return raw.get("result", {})

    async def js(self, expr: str, session: str, wait: bool = True) -> Any:
        r = await self.call("Runtime.evaluate",
                            {"expression": expr, "returnByValue": True, "awaitPromise": wait},
                            session)
        if r.get("exceptionDetails"):
            d = r["exceptionDetails"]
            raise RuntimeError(d.get("exception", {}).get("description") or d.get("text", "js error"))
        return r.get("result", {}).get("value")


# ---------------------------------------------------------------------------
# Where does the game live?
# ---------------------------------------------------------------------------
def origin_for(project: Path) -> tuple[str, str]:
    """(origin, how) for a project — its own dev server if one is up, else a served folder."""
    try:
        from . import dev_server
        servers = dev_server.running_servers(project)
        if servers:
            return servers[0]["url"].rstrip("/"), "the project's running server"
    except Exception:
        pass
    root = _entry_dir(project)
    from . import preview_server
    port = preview_server.serve_dir(str(root))
    return f"http://127.0.0.1:{port}", f"a static server on {root.name or root}"


def _entry_dir(project: Path) -> Path:
    """The folder whose index.html is most likely the game."""
    for rel in ("", "public", "build", "dist", "deploy", "src", "www", "game"):
        p = project / rel if rel else project
        if (p / "index.html").exists():
            return p
    return project


# ---------------------------------------------------------------------------
# The render
# ---------------------------------------------------------------------------
DEFAULT_TIMES = [0, 150, 350, 600, 900, 1300]
DEFAULT_WARMUP = 1200          # ms of stepped boot before the first sampled frame


async def _run(spec: dict) -> dict:
    ws_url = _ensure_browser()
    mode = spec.get("mode") or "scene"
    w, h = spec.get("size") or [640, 400]
    w, h = max(64, int(w)), max(64, int(h))
    bg = str(spec.get("background") or "#12151c")
    times = [int(t) for t in (spec.get("times") or DEFAULT_TIMES)][:16]
    times = sorted({max(0, t) for t in times})
    notes: list[str] = []

    async with websockets.connect(ws_url, max_size=96 * 1024 * 1024,
                                  open_timeout=20, close_timeout=5) as ws:
        cdp = _Cdp(ws)
        skey = str(spec.get("session") or "")
        tid, fresh = "", True
        if skey and skey in _tabs and time.time() - _tabs[skey]["seen"] < _TAB_IDLE:
            try:                                  # the tab may have been closed under us
                await cdp.call("Target.getTargetInfo", {"targetId": _tabs[skey]["tid"]})
                tid, fresh = _tabs[skey]["tid"], False
            except Exception:
                _tabs.pop(skey, None)
        if not tid:
            # `newWindow`: a current Chrome refuses a size on a plain tab; see live._open_target.
            tid = (await cdp.call("Target.createTarget",
                                  {"url": "about:blank", "width": w, "height": h, "newWindow": True}))["targetId"]
        _inflight.add(tid)
        try:
            sid = (await cdp.call("Target.attachToTarget",
                                  {"targetId": tid, "flatten": True}))["sessionId"]
            await cdp.call("Page.enable", {}, sid)
            await cdp.call("Runtime.enable", {}, sid)
            # The browser outlives a review, so a second review of the same file would otherwise
            # be served the first one's modules out of the memory cache -- and report that an edit
            # changed nothing. A review must always look at what is on disk now.
            try:
                await cdp.call("Network.enable", {}, sid)
                await cdp.call("Network.setCacheDisabled", {"cacheDisabled": True}, sid)
            except Exception:
                pass
            await cdp.call("Emulation.setDeviceMetricsOverride",
                           {"width": w, "height": h,
                            "deviceScaleFactor": float(spec.get("scale") or _SCALE),
                            "mobile": False}, sid)
            url = spec.get("url") or ""
            if fresh:
                await cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": SHIM}, sid)
                await cdp.call("Page.navigate", {"url": url or "about:blank"}, sid)
                await asyncio.sleep(_NAV_SETTLE)
            await cdp.js(f"__review.seed({int(spec.get('seed') or 1234567)})", sid)

            # Warm-up. A game boots asynchronously: it fetches atlases, decodes audio, and walks a
            # loading state machine that only advances on a frame tick. Both need to happen before
            # the first sampled frame, and they need each other -- real time alone leaves the state
            # machine frozen (its frame callbacks are ours now), and stepping alone gives the
            # network no chance to answer. So step in small beats with a real pause between them.
            # Without this the sheet is eight photographs of a loading bar.
            warm = max(0, int(spec.get("warmup_ms", 1200)))
            all_early = sorted([e for e in (spec.get("input") or []) if int(e.get("at", 0)) < 0],
                               key=lambda e: int(e.get("at", 0)))

            async def warmup():
                """Boot the game and get it past whatever stands between us and the subject.

                Returns (ms spent, whether the game declared itself ready). Written as a helper
                because the probe pass reloads the page, and a second warm-up that forgot to
                replay the title-screen keypress silently reviewed the menu."""
                early = list(all_early)
                done = 0
                declared = False
                while done < warm:
                    await cdp.js("__review.step(16)", sid, wait=False)
                    done += 16
                    while early and warm + int(early[0].get("at", 0)) <= done:
                        await _send_input(cdp, sid, early.pop(0))
                    await asyncio.sleep(0.008)
                    # The game gets to say when it is ready. Only a game that opted in returns a
                    # boolean; everything else returns null and rides the fixed wait out.
                    if not declared and done % 96 == 0 and not early:
                        r = await cdp.js("__review.gameReady()", sid)
                        if r is True:
                            return done, True
                        if r is False:
                            warm_more = done + 400        # it says not yet: give it room
                            if warm_more > done:
                                done = min(done, warm_more)
                for e in early:                            # anything the loop was too short for
                    await _send_input(cdp, sid, e)
                return done, declared

            async def arm():
                """Put the subject in front of the camera.

                Three ways in, weakest cooperation first: a named action the game registered, a
                raw expression the caller wrote (needs nothing from the game at all), and the
                reset that lets a warm tab fire the same effect again from a clean state."""
                if spec.get("reset"):
                    await cdp.js("__review.act(%s)" % json.dumps(str(spec["reset"])), sid)
                    await asyncio.sleep(0.03)
                if spec.get("action"):
                    got = await cdp.js("__review.act(%s)" % json.dumps(str(spec["action"])), sid)
                    if not (got or {}).get("ok"):
                        return got or {"ok": False, "error": "action failed"}
                    notes.append("fired action `%s`" % spec["action"])
                if spec.get("js"):
                    await cdp.js(str(spec["js"]), sid)
                    notes.append("ran the trigger expression")
                return {"ok": True}

            if fresh:
                spent, declared = await warmup()
                if declared:
                    notes.append(f"the game declared itself ready after {spent}ms")
                elif warm:
                    notes.append(f"warmed up {warm}ms before the first sampled frame")
            else:
                notes.append("reused a warm tab (no reload, no warm-up)")
            armed = await arm()
            if not armed.get("ok"):
                return {"ok": False, "error": armed.get("error", "could not arm the review"),
                        "available": armed.get("available", [])}

            if mode == "isolate":
                got = await cdp.js(
                    "__review.mount(%s, %s, %s, %d, %d)" % (
                        json.dumps(spec.get("module") or "/review/targets.js"),
                        json.dumps(spec.get("target") or ""), json.dumps(bg), w, h), sid)
                if not (got or {}).get("ok"):
                    return {"ok": False, "error": (got or {}).get("error", "mount failed"),
                            "available": (got or {}).get("available", [])}
                notes.append(f"isolated `{got.get('name')}` on {bg}")
                await asyncio.sleep(0.15)        # let the mount's own async work land
            else:
                notes.append("the running game, driven by a stepped clock")

            # Find the busy moment rather than guessing it. Probing advances the clock, so the
            # page is reloaded and warmed again before the real captures -- the probe is a scout,
            # not the run. Only worth its second warm-up when the caller does not know the timing.
            probe_curve = []
            if spec.get("auto_times"):
                dt = max(8, int(spec.get("probe_step_ms") or 25))
                n = max(4, min(200, int(spec.get("probe_steps") or 60)))
                probe_curve = await cdp.js(f"__review.probe({dt}, {n})", sid) or []
                if probe_curve:
                    peak = max(probe_curve, key=lambda r: r.get("ink", 0))
                    p = int(peak["t"])
                    top = float(peak.get("ink", 0))
                    # The event's own extent: where activity rises past a quarter of its peak and
                    # falls back under it. Spreading samples over the whole probed window instead
                    # spends five of six captures on nothing happening.
                    live = [int(r["t"]) for r in probe_curve
                            if float(r.get("ink", 0)) >= max(0.002, top * 0.25)]
                    lo, hi = (min(live), max(live)) if live else (0, int(probe_curve[-1]["t"]))
                    k = 6
                    grid = ([lo + round((hi - lo) * i / (k - 1)) for i in range(k)]
                            if hi > lo else [lo])
                    j = min(range(len(grid)), key=lambda i: abs(grid[i] - p))
                    grid[j] = p                       # the peak is never missed
                    times = sorted({max(0, t) for t in ([0] if lo > 60 else []) + grid})
                    notes.append("probed %d steps; peak %.1f%% at t=%.2fs, event %.2f-%.2fs"
                                 % (n, top * 100, p / 1000, lo / 1000, hi / 1000))
                    await cdp.call("Page.navigate", {"url": url or "about:blank"}, sid)
                    await asyncio.sleep(_NAV_SETTLE)
                    await cdp.js(f"__review.seed({int(spec.get('seed') or 1234567)})", sid)
                    await warmup()          # same boot, inputs included -- not the title screen
                    await arm()             # and the same trigger, or we probe an empty scene

            inputs = sorted([e for e in (spec.get("input") or [])
                             if int(e.get("at", 0)) >= 0], key=lambda e: int(e.get("at", 0)))
            # A clip and a sheet from ONE pass. Capturing densely and picking the sheet frames
            # out of that set costs ~9ms per extra frame and avoids a second boot entirely.
            sheet_times = list(times)
            if spec.get("clip"):
                fps = max(5, min(60, int(spec.get("clip_fps") or 30)))
                dt = int(round(1000 / fps))
                end = max(times) if times else 1200
                times = sorted({0} | {t for t in range(0, end + dt, dt)} | set(sheet_times))

            shots: list[tuple[int, str]] = []
            clock = 0                            # sample-timeline ms; the warm-up is behind us
            ii = 0
            for t in times:
                while ii < len(inputs) and int(inputs[ii].get("at", 0)) <= t:
                    await _send_input(cdp, sid, inputs[ii])
                    ii += 1
                if t > clock:
                    await cdp.js(f"__review.step({t - clock})", sid, wait=False)
                    clock = t
                elif t == 0:
                    await cdp.js("__review.step(0)", sid, wait=False)
                await asyncio.sleep(0.02)        # let async uploads land before we read pixels
                data = await cdp.js("__review.grab()", sid)
                if not data:
                    shot = await cdp.call("Page.captureScreenshot",
                                          {"format": "png", "captureBeyondViewport": False}, sid)
                    data = "data:image/png;base64," + shot.get("data", "")
                    if t == times[0]:
                        notes.append("photographed the page, not the canvas — this game draws "
                                     "part of itself in HTML, and only the page shows both")
                shots.append((t, data))

            errs = await cdp.js("(window.__review.errors||[]).slice(0,3)", sid) or []
            if errs:
                notes.append("frame callback threw: " + "; ".join(str(e)[:120] for e in errs))
            # The static server falls back to a file index when a folder has no index.html.
            # That page renders perfectly and measures perfectly, so a review of it looks like a
            # success and tells the reviewer nothing about the game.
            title = str(await cdp.js("(document.title||'').slice(0,60)", sid) or "")
            if title.startswith("Directory listing for"):
                notes.append("NO GAME HERE — this folder has no index.html, so the picture is the "
                             "file listing the static server produced. Point `project` at the "
                             "folder that holds index.html.")
            missed = await cdp.js("__review.misses()", sid) or []
            if missed:
                short = [m.rsplit("/", 1)[-1] or m for m in missed[:4]]
                notes.append("MISSING FILE: the page asked for %s and did not get %s — the "
                             "picture below is the page WITHOUT %s"
                             % (", ".join(short), "them" if len(short) > 1 else "it",
                                "them" if len(short) > 1 else "it"))
            shape = await cdp.js("__review.shape()", sid) or {}
            if shape.get("canvas") and shape.get("w") == 300 and shape.get("h") == 150:
                notes.append("the canvas is still 300x150, the browser's default size — the game "
                             "had not sized it when the frames were taken; raise `warmup_ms`")
            return {"ok": True, "shots": shots, "sheet_times": sheet_times, "notes": notes,
                    "shape": shape, "probe": probe_curve}
        finally:
            _inflight.discard(tid)
            if skey:
                _tabs[skey] = {"tid": tid, "seen": time.time()}
            else:
                try:
                    await cdp.call("Target.closeTarget", {"targetId": tid})
                except Exception:
                    pass
            # ON EVERY RENDER, not only on the ones that name a session. The reap used to sit
            # inside the `if skey:` branch, so a warm tab was only ever closed by a LATER render
            # that also passed a session — and a person who used a session once and never again
            # left a whole game running in the browser for the rest of the day.
            for k, v in list(_tabs.items()):       # do not hoard tabs nobody came back to
                if time.time() - v["seen"] > _TAB_IDLE:
                    _tabs.pop(k, None)
                    try:
                        await cdp.call("Target.closeTarget", {"targetId": v["tid"]})
                    except Exception:
                        pass
            try:
                await sweep_orphan_pages(cdp)
            except Exception:
                pass


async def _send_input(cdp: _Cdp, sid: str, ev: dict) -> None:
    kind = str(ev.get("type") or "").lower()
    try:
        if kind == "eval":
            await cdp.js(str(ev.get("js") or ""), sid)
            return
        if kind == "action":
            await cdp.js("__review.act(%s)" % json.dumps(str(ev.get("action") or "")), sid)
            return
        if kind in ("keydown", "keyup"):
            key = str(ev.get("key") or "")
            await cdp.call("Input.dispatchKeyEvent", {
                "type": "keyDown" if kind == "keydown" else "keyUp",
                "key": key, "code": ev.get("code") or key,
                "windowsVirtualKeyCode": int(ev.get("keyCode") or 0),
            }, sid)
        elif kind == "key":                       # press and release in one beat
            await _send_input(cdp, sid, {**ev, "type": "keydown"})
            await _send_input(cdp, sid, {**ev, "type": "keyup"})
        elif kind in ("click", "mousedown", "mouseup", "mousemove"):
            m = {"click": "mousePressed", "mousedown": "mousePressed",
                 "mouseup": "mouseReleased", "mousemove": "mouseMoved"}[kind]
            base = {"type": m, "x": int(ev.get("x") or 0), "y": int(ev.get("y") or 0),
                    "button": ev.get("button") or "left", "clickCount": 1}
            await cdp.call("Input.dispatchMouseEvent", base, sid)
            if kind == "click":
                await cdp.call("Input.dispatchMouseEvent", {**base, "type": "mouseReleased"}, sid)
    except Exception:
        pass                                      # a rejected synthetic event must not kill the run


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------
# A plate whose own frames move is not a plate. `auto_plate` derives one by dropping the trigger,
# which is correct for a spell that only fires when cast and silently wrong for an effect that
# loops on its own -- the "clean" run still contains the fireball, so diffing against it cancels
# part of the very thing being measured and coverage reads LOW. Measured on the same scene:
# 0.000 when the effect is trigger-gated, 0.032 when it auto-plays.
_PLATE_MOVES = 0.004


def _plate_is_clean(plate: list) -> bool:
    import numpy as np
    if not plate or len(plate) < 2:
        return True
    a = [np.asarray(f.convert("RGB"), dtype=np.int16) for f in plate]
    move = float(np.mean([np.abs(a[i] - a[i - 1]).mean() for i in range(1, len(a))])) / 255
    return move < _PLATE_MOVES


def _background(frames: list, explicit=None):
    """A per-pixel plate rather than one corner pixel.

    Reading the backdrop from pixel (1,1) is right in isolate mode, where we chose the backdrop,
    and close to worthless in a real scene. On a game whose corner is sky and whose field is a
    painted static backdrop, `ink` then measures "how much of this picture is not sky" -- a large
    number that barely moves, which answers nothing.

    The per-pixel median across the sampled frames is a plate instead: whatever persists is
    background, whatever is transient is signal. It needs no cooperation from the game. When the
    caller CAN turn the effect off, pass those frames as `explicit` and the real clean plate is
    used, which is strictly better."""
    import numpy as np
    src = explicit if explicit else frames
    if len(src) >= 3:
        stack = np.stack([np.asarray(f.convert("RGB"), dtype=np.uint8) for f in src])
        return np.median(stack, axis=0).astype(np.int16)
    px = src[0].convert("RGB").getpixel((1, 1))
    return np.array(px, dtype=np.int16)


def _measure(frames: list, bg_rgb, backdrop=None) -> list[dict]:
    """Per-frame figures a reviewer can act on without looking at the image.

    Two coverage numbers, because they answer two different questions and a scene review needs
    the second one:

    `ink` is the share of the frame that differs from the PLATE — what changed. It is the right
    number for an effect, and it is near zero for a static-camera 3D scene by construction, since
    the median plate then contains the whole world. Judging a scene by `ink` alone reports an
    empty picture for a perfectly good one.

    `fill` is the share that differs from the page's own BACKDROP — what is drawn at all. It does
    not care whether the subject moves, so it is the number that says "yes, this renders".

    `motion` is measured inside the drawn region rather than across the whole frame. A few ships
    drifting across a mostly black 1280x800 screen move a vanishing fraction of all pixels, and
    a whole-frame average reports that real movement as zero.
    """
    import numpy as np
    out: list[dict] = []
    prev = None
    prev_drawn = None
    back = np.array(backdrop, dtype=np.int16) if backdrop is not None else None
    for img in frames:
        a = np.asarray(img.convert("RGB"), dtype=np.int16)
        bg = bg_rgb if getattr(bg_rgb, "ndim", 0) == 3 else np.array(bg_rgb, dtype=np.int16)
        diff = np.abs(a - bg).sum(axis=2)
        mask = diff > 24                          # "not the backdrop", tolerant of dithering
        ink = float(mask.mean())
        drawn = (np.abs(a - back).sum(axis=2) > 24) if back is not None else mask
        lum = (0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2])
        row = {"ink": round(ink, 4),
               "fill": round(float(drawn.mean()), 4),
               "lum_mean": round(float(lum.mean()), 1),
               "lum_max": round(float(lum.max()), 1)}
        if mask.any():
            ys, xs = np.where(mask)
            H, W = mask.shape
            row["bbox"] = [round(float(xs.min() / W), 3), round(float(ys.min() / H), 3),
                           round(float((xs.max() - xs.min() + 1) / W), 3),
                           round(float((ys.max() - ys.min() + 1) / H), 3)]
            sub = a[mask]
            q = (sub // 32 * 32).astype(np.uint8)
            cols, counts = np.unique(q.reshape(-1, 3), axis=0, return_counts=True)
            top = counts.argsort()[::-1][:4]
            row["palette"] = ["#%02x%02x%02x" % tuple(int(v) for v in cols[i]) for i in top]
        else:
            row["bbox"] = [0.0, 0.0, 0.0, 0.0]
            row["palette"] = []
        if prev is None:
            row["motion"] = 0.0
        else:
            d = np.abs(a - prev).mean(axis=2)
            here = drawn | prev_drawn                # the subject, this frame or the last
            row["motion"] = round(float(d[here].mean() / 255.0) if here.any() else 0.0, 4)
        prev_drawn = drawn
        prev = a
        out.append(row)
    return out


def _verdict(rows: list[dict], times: list[int]) -> list[str]:
    """The handful of conclusions worth stating in words rather than leaving in a table."""
    say: list[str] = []
    if not rows:
        return say
    inks = [r["ink"] for r in rows]
    fills = [r.get("fill", r["ink"]) for r in rows]
    peak = max(range(len(inks)), key=lambda i: inks[i])
    say.append(f"peak coverage {inks[peak] * 100:.1f}% at t={times[peak] / 1000:.2f}s")
    if max(r["lum_max"] for r in rows) < 1.0:
        # Pure black, every pixel, every frame. `fill` cannot catch this on its own: measured
        # against a dark backdrop, black differs from it enough to count as painted, so an empty
        # capture reported 100% drawn. Luminance settles it.
        say.append("BLANK — every sampled frame is pure black, so nothing was captured. The "
                   "canvas was empty, the game had not painted yet, or the subject is behind a "
                   "loading screen. Raise `warmup_ms`, or check the page really renders.")
    elif max(fills) < 0.005:
        say.append("NOTHING VISIBLE — under 0.5% of the frame is drawn at any sampled time. "
                   "Check the target actually renders before judging its art.")
    elif max(inks) < 0.005:
        # The picture is there; it simply does not CHANGE, so the median plate swallowed it.
        # Saying "nothing visible" here sends the reviewer hunting a bug that does not exist.
        say.append("STILL SCENE — the frame is %.1f%% drawn, but little changes between samples, "
                   "so the auto plate absorbed the picture and `ink` reads ~0 by construction. "
                   "Read `fill` to judge the composition. To measure an effect, fire it with "
                   "`action` or `js`, or pass `plate`/`auto_plate` for a real clean shot."
                   % (max(fills) * 100))
    if max(r["motion"] for r in rows) < 0.002 and len(rows) > 1 and max(inks) >= 0.005:
        say.append("STATIC — consecutive frames are near-identical, so the clock is being stepped "
                   "but nothing animates. Either the effect is not time-driven or it never started.")
    if 0.005 <= max(inks) < 0.05 and max(r["motion"] for r in rows) > 0.002:
        say.append("the scene never rests (a scrolling or animated backdrop), so coverage is "
                   "measured against a blurred plate and reads low. Trust `motion` here, or pass "
                   "`plate` to diff against a clean shot.")
    if inks[-1] > 0.6 * max(inks) and len(rows) > 2:
        say.append("still going at the last sampled time — extend `times` to see it finish")
    return say


# ---------------------------------------------------------------------------
# The contact sheet
# ---------------------------------------------------------------------------
def _font(size: int):
    from PIL import ImageFont
    try:
        return ImageFont.truetype("consola.ttf", size)
    except Exception:
        pass
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        pass
    try:
        return ImageFont.load_default(size=size)   # Pillow >= 10.1
    except Exception:
        return ImageFont.load_default()


async def sweep_orphan_pages(cdp, keep: set = frozenset()) -> int:
    """Close every page in the shared browser that nothing in the Studio owns.

    A leaked page is not idle: a WebGL game keeps rendering, keeps its textures, and keeps its
    share of an 8 GB card. Twelve copies of one game were found open at once here — 2.5 GB of VRAM
    and 4.8 GB of RAM for tabs no one could see, because the only cleanup that existed ran inside
    a render that passed a session name, and a run of renders without one swept nothing.

    What is OWNED, and therefore never touched:
      - a warm tab someone may come back to (`_tabs`),
      - a tab a render is using this second (`_inflight`),
      - a live-link tab: that is another agent's game, opened deliberately and held on purpose,
      - anything the caller names in `keep`.
    Everything else is a page no code can reach any more, so closing it loses nothing.
    """
    owned = {v.get("tid") for v in _tabs.values()} | set(_inflight) | set(keep)
    try:                                  # the live link's tabs belong to whoever opened them
        from . import live
        owned |= {e.get("target") for e in live._tabs.values() if e.get("target")}
    except Exception:
        pass
    try:
        targets = (await cdp.call("Target.getTargets", {}))["targetInfos"]
    except Exception:
        return 0
    closed = 0
    for t in targets:
        if t.get("type") != "page" or t.get("targetId") in owned:
            continue
        try:
            await cdp.call("Target.closeTarget", {"targetId": t["targetId"]})
            closed += 1
        except Exception:
            pass
    return closed


def _plan(fw: int, fh: int, n: int, budget: int = 0) -> tuple:
    """(columns, cell width) — the biggest cells whose sheet still fits the pixel budget.

    Prefers wide cells over many columns, never proposes a cell wider than the frame itself
    (upscaling costs pixels and invents detail that was never rendered), and never returns a sheet
    whose long edge exceeds what a vision model will keep."""
    budget = budget or 1_120_000
    for min_aspect in (0.95, 0.0):     # landscape-or-square first; anything, rather than nothing
        got = _plan_at(fw, fh, n, budget, min_aspect)
        if got[1]:
            return got
    return (min(4, n), max(120, min(fw, 240)))


def _plan_at(fw: int, fh: int, n: int, budget: int, min_aspect: float) -> tuple:
    best = (0, 0)
    for cols in range(1, min(4, n) + 1):
        rows = (n + cols - 1) // cols
        top = min(fw, _CELL_MAX, (_SHEET_MAX - (cols + 1) * 10) // cols)
        cw = top - (top % 10)
        while cw >= 120:
            ch = max(1, int(cw * fh / max(1, fw)))
            W = cols * (cw + 10) + 10
            H = 58 + rows * (ch + 32) + _PLAY_H + 40
            if W * H <= budget and max(W, H) <= _SHEET_MAX and W >= H * min_aspect:
                break
            cw -= 10
        if cw < 120:
            continue
        if cw > best[1] or (cw == best[1] and cols > best[0]):
            best = (cols, cw)
    return best


def _fit(im, cw: int, ch: int):
    """Down to the cell with a proper filter; never up."""
    from PIL import Image
    if im.size == (cw, ch):
        return im
    if im.width > cw:
        return im.resize((cw, ch), Image.LANCZOS)
    return im


def _sheet(frames: list, times: list, title: str, sub: str, budget: int = 0):
    """One image: the grid, then the same frames at the size the player sees them.

    The second row is the whole reason the sheet exists — art that reads beautifully at 600px
    routinely disappears at the 96px it actually ships at, and no hero render ever says so."""
    from PIL import Image, ImageDraw
    n = len(frames)
    fw, fh = frames[0].size
    cols, cw = _plan(fw, fh, n, budget)
    ch = max(1, int(cw * fh / max(1, fw)))
    rows = (n + cols - 1) // cols

    f_lab, f_sub, f_title = _font(15), _font(13), _font(19)
    pad, lab, head = 10, 22, 58
    W = cols * (cw + pad) + pad
    H = head + rows * (ch + lab + pad) + pad + (_PLAY_H + lab + 14)

    sheet = Image.new("RGB", (W, H), (14, 16, 22))
    d = ImageDraw.Draw(sheet)
    d.text((pad, 12), title, font=f_title, fill=(232, 236, 244))
    d.text((pad, 36), sub, font=f_sub, fill=(126, 138, 158))

    for i, im in enumerate(frames):
        r, c = divmod(i, cols)
        x = pad + c * (cw + pad)
        y = head + r * (ch + lab + pad)
        thumb = _fit(im, cw, ch)
        d.rectangle([x - 1, y - 1, x + cw, y + ch], outline=(38, 43, 54))
        sheet.paste(thumb, (x + (cw - thumb.width) // 2, y + (ch - thumb.height) // 2))
        # A number is a moment in a capture; a string is a named view from the forge, where
        # "3q" and "top" are the labels that mean something and a timestamp would not.
        cap = times[i] if isinstance(times[i], str) else "t=%.2fs" % (times[i] / 1000)
        d.text((x + 2, y + ch + 4), cap, font=f_lab, fill=(150, 162, 182))

    y = head + rows * (ch + lab + pad) + 6
    d.text((pad, y), "as the player sees it", font=f_sub, fill=(126, 138, 158))
    y += lab
    x = pad
    for im in frames:
        small = im.copy()
        small.thumbnail((_PLAY_H * 3, _PLAY_H), Image.LANCZOS)
        if x + small.width > W - pad:
            break
        sheet.paste(small, (x, y))
        x += small.width + 8
    return sheet


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def out_dir(project_id: str) -> Path:
    import re
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", project_id or "project")
    d = DATA_DIR / "review" / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


def render(project: str, spec: dict, project_id: str = "") -> dict:
    """Render one review. Returns the sheet path, the numbers, and the conclusions."""
    from PIL import Image
    import io

    root = Path(project)
    if not root.exists():
        return {"ok": False, "error": f"no such folder: {project}"}

    spec = dict(spec or {})
    want = str(spec.get("quality") or settings.get("cc_review_quality") or "normal").lower()
    budget, scale = QUALITY.get(want, QUALITY["normal"])
    spec.setdefault("scale", scale)
    how = ""
    if not spec.get("url"):
        try:
            origin, how = origin_for(root)
        except Exception as e:
            return {"ok": False, "error": f"could not find a way to serve the project: {e}"}
        if spec.get("mode") == "isolate":
            # Any same-origin document will do — the target is imported as a module, and loading
            # the game's own index page first would only mean tearing it down again.
            spec["url"] = origin + "/__studio_review__"
        else:
            spec["url"] = origin + "/"

    # A clean plate, when the caller can produce one. `plate` is a set of overrides merged onto
    # this spec -- a target with the effect removed, an input sequence that holsters the weapon --
    # rendered first so the real run can be diffed against a scene that genuinely lacks the thing
    # under review. This is strictly better than any estimate, and it is the only way to get an
    # honest number out of a scene that never rests.
    plate_spec = spec.pop("plate", None)
    auto_plate = bool(spec.pop("auto_plate", False))
    _note_use(project, spec, project_id)
    _slots.acquire()
    try:
        res = asyncio.run(_run(spec))
        # The plate runs AFTER, not before, so it can borrow the times the real run settled on --
        # the probe only knows where the event is once it has seen it. `auto_plate` derives the
        # plate by removing the trigger: same boot, same frames, minus the thing being reviewed.
        # It never reuses the warm tab, or the effect just fired would still be on screen.
        plate_res = None
        if res.get("ok") and (plate_spec or auto_plate):
            p = {**spec, **(plate_spec if isinstance(plate_spec, dict) else {})}
            for k in ("auto_times", "clip", "session"):
                p.pop(k, None)
            if auto_plate:
                p["js"] = ""
                p["action"] = ""
            p["times"] = res.get("sheet_times") or [t for t, _ in res["shots"]]
            plate_res = asyncio.run(_run(p))
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        _slots.release()
    if not res.get("ok"):
        return res

    def _dec(data: str):
        return Image.open(io.BytesIO(base64.b64decode(data.split(",", 1)[1]))).convert("RGB")

    decoded = [(t, _dec(d)) for t, d in res["shots"]]
    want = set(res.get("sheet_times") or [t for t, _ in decoded])
    picked = [(t, im) for t, im in decoded if t in want] or decoded
    times = [t for t, _ in picked]
    frames = [im for _, im in picked]
    plate = None
    dirty_plate = False
    if plate_res and plate_res.get("ok"):
        plate = [_dec(d) for _, d in plate_res["shots"]]
        if _plate_is_clean(plate):
            res.setdefault("notes", []).append(
                "compared against a clean plate (%d frames)" % len(plate))
        else:
            plate, dirty_plate = None, True
            res.setdefault("notes", []).append(
                "the plate was discarded: it still contained the effect")
    backdrop = (res.get("shape") or {}).get("backdrop")
    rows = _measure(frames, _background(frames, plate), backdrop) if frames else []
    if dirty_plate:
        res.setdefault("findings_extra", []).append(
            "PLATE DISCARDED — the run without the trigger still showed the effect, so this "
            "effect is not trigger-gated. `auto_plate` cannot help here and would have "
            "under-reported coverage; the per-frame median was used instead.")

    label = str(spec.get("label") or spec.get("target") or spec.get("mode") or "review")
    import re as _re
    stem = _re.sub(r"[^A-Za-z0-9._-]", "-", label)[:48] or "review"
    d = out_dir(project_id or root.name)
    path = d / f"{stem}-{int(time.time())}.png"
    # Cut on a separator, never mid-word. The notes grew and the header started ending in
    # things like "photographed the page, no", which reads as a fault rather than a sentence.
    _bits = [x for x in (how, *res.get("notes", [])) if x]
    sub = " · ".join(_bits)
    if len(sub) > 200:
        sub, keep = "", []
        for b in _bits:
            if len(" · ".join(keep + [b])) > 197:
                break
            keep.append(b)
        sub = " · ".join(keep) + (" · …" if len(keep) < len(_bits) else "")
    sheet_img = _sheet(frames, times, label, sub, budget)
    sheet_img.save(path, optimize=True)

    # The sheet answers "does the arc work". Judging craft — edge quality, banding, a font that
    # does not hint — needs the pixels the sheet had to give up, so the busiest frame is also kept
    # at full capture resolution. It is a separate file on purpose: the reviewer opens it only
    # when the sheet raises a question that detail can settle.
    peak_path = ""
    if frames:
        i = max(range(len(rows)), key=lambda k: rows[k]["ink"])
        _cols, cell_w = _plan(frames[0].width, frames[0].height, len(frames), budget)
        if frames[i].width > cell_w:
            peak_path = str(d / f"{stem}-peak-t{times[i]}ms.png")
            frames[i].save(peak_path, optimize=True)
        # Every frame has to fit one pixel budget, so asking for more moments makes each one
        # smaller. That is the right trade for an effect and the wrong one for an interface:
        # a HUD label at half size is a smear, and the reviewer quietly judges art it cannot
        # actually read. Say so, with the two ways out.
        shrink = frames[0].width / max(1, cell_w)
        if shrink >= 1.4:
            res.setdefault("findings_extra", []).append(
                "SMALL IN THE SHEET — each frame is %d px wide, down from %d (%.1f×), so small "
                "interface text will not survive. To read the UI: ask for fewer `times`, or "
                "`\"quality\":\"high\"`, or open `peak` which is saved at full size."
                % (cell_w, frames[0].width, shrink))

    # The clip is for a person, not for the reviewer: a model reads images, so handing it a video
    # only means decoding back to frames, which is what the sheet already is. What the clip adds is
    # that a HUMAN can watch the effect -- and because the clock is driven, two runs produce
    # identical files, which no real-time screen capture can offer. Animated WebP: 24-bit, so a
    # fireball's gradients survive, and no new dependency (GIF would band it to 256 colours).
    clip_path = ""
    if spec.get("clip") and len(decoded) > 2:
        fps = max(5, min(60, int(spec.get("clip_fps") or 30)))
        cap = max(240, min(1280, int(spec.get("clip_width") or 640)))
        seq = [im if im.width <= cap else
               im.resize((cap, max(1, round(cap * im.height / im.width))), Image.LANCZOS)
               for _, im in decoded]
        clip_path = str(d / f"{stem}.webp")
        seq[0].save(clip_path, format="WEBP", save_all=True, append_images=seq[1:],
                    duration=int(round(1000 / fps)), loop=0, quality=80, method=4)

    return {"ok": True, "sheet": str(path), "peak": peak_path, "clip": clip_path,
            "clip_frames": len(decoded) if clip_path else 0,
            "capture": list(frames[0].size) if frames else [],
            "frames": len(frames), "times": times,
            "mode": spec.get("mode") or "scene", "url": spec.get("url"),
            "shape": res.get("shape", {}), "notes": res.get("notes", []),
            "metrics": rows,
            "findings": res.get("findings_extra", []) + _verdict(rows, times)}


def actions(project: str, url: str = "") -> dict:
    """Names the game registered on window.__review.actions, read from the live page."""
    root = Path(project)
    if not root.exists():
        return {"ok": False, "error": f"no such folder: {project}"}
    try:
        origin, _ = origin_for(root)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    async def go():
        ws_url = _ensure_browser()
        async with websockets.connect(ws_url, max_size=8 * 1024 * 1024) as ws:
            cdp = _Cdp(ws)
            t = await cdp.call("Target.createTarget", {"url": "about:blank"})
            try:
                sid = (await cdp.call("Target.attachToTarget",
                                      {"targetId": t["targetId"], "flatten": True}))["sessionId"]
                await cdp.call("Page.enable", {}, sid)
                await cdp.call("Runtime.enable", {}, sid)
                await cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": SHIM}, sid)
                await cdp.call("Page.navigate", {"url": url or (origin + "/")}, sid)
                await asyncio.sleep(_NAV_SETTLE)
                for _ in range(60):               # the registry appears when the game boots
                    await cdp.js("__review.step(16)", sid, wait=False)
                    await asyncio.sleep(0.008)
                    got = await cdp.js("__review.actionList()", sid)
                    if got:
                        return got
                return []
            finally:
                try:
                    await cdp.call("Target.closeTarget", {"targetId": t["targetId"]})
                except Exception:
                    pass

    _slots.acquire()
    try:
        found = asyncio.run(go())
        return {"ok": True, "actions": found, "missing": not found}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        _slots.release()


def targets(project: str, module: str = "/review/targets.js") -> dict:
    """What can be isolated in this project? Empty means nobody has written the file yet."""
    root = Path(project)
    if not (root / module.lstrip("/")).exists():
        return {"ok": True, "targets": [], "file": str(root / module.lstrip("/")),
                "missing": True}
    try:
        origin, _ = origin_for(root)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    async def go():
        ws_url = _ensure_browser()
        async with websockets.connect(ws_url, max_size=8 * 1024 * 1024) as ws:
            cdp = _Cdp(ws)
            t = await cdp.call("Target.createTarget", {"url": "about:blank"})
            try:
                sid = (await cdp.call("Target.attachToTarget",
                                      {"targetId": t["targetId"], "flatten": True}))["sessionId"]
                await cdp.call("Page.enable", {}, sid)
                await cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": SHIM}, sid)
                await cdp.call("Page.navigate", {"url": origin + "/__studio_review__"}, sid)
                await asyncio.sleep(_NAV_SETTLE)
                return await cdp.js("__review.list(%s)" % json.dumps(module), sid)
            finally:
                try:
                    await cdp.call("Target.closeTarget", {"targetId": t["targetId"]})
                except Exception:
                    pass

    _slots.acquire()
    try:
        return {"ok": True, "targets": asyncio.run(go()) or [], "missing": False}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        _slots.release()

"""A live link into the running game — read it, drive it, change it while it runs.

The visual review harness answers "does this read in play". It drives a fake clock, takes the
frames you name, and hands back a contact sheet. That is the right tool for judging art and the
wrong tool for everything else: it cannot tell you WHY the mountain is black, because it returns
pixels and pixel statistics, and a picture cannot say `material.diffuse is #000000`.

This module is the other half. It keeps one tab of the game open on the REAL clock — the same
headless Chrome the review harness already runs, one more tab, no second process — and lets an
agent ask the running game questions and change it on the spot:

    curl .../api/live/open  -d '{"project":"…"}'
      -> {"engine":"playcanvas","engine_version":"PlayCanvas 2.21.4","found_at":"__game.renderer.app"}
    curl .../api/live/eval  -d '{"project":"…","js":"__game.renderer.app.scene.ambientLight"}'
      -> {"value":{"__type":"Color","r":0.604,"g":0.643,"b":0.741,"a":1}}
    curl .../api/live/eval  -d '{"project":"…","js":"__game.renderer.app.scene.ambientLight.set(1,0,0)"}'

The last one is the point. An agent can try a value in the live game in 200ms, look at it, and
only then write it to the file — instead of editing, rebuilding, screenshotting and guessing.

`found_at` is not decoration. A game built with a bundler has no `window.pc` and no `window.THREE`:
the engine is an ES module and its app object lives in module scope, unreachable from outside. So
the shim hunts it — it diffs the page's globals against a blank iframe's and walks what is left.
On the game this was built against that lands on `__game.renderer.app`, a path no list of likely
names would ever have held.

Nothing here holds a socket open between calls. The TAB is what persists; every request opens its
own short-lived connection to the browser, does one job and closes. That is why a backend restart,
a crashed request or a stalled agent can never leak a connection, and why the game keeps running
through all of it.
"""
from __future__ import annotations

import asyncio
import base64
import errno
import json
import math
import os
import re
import select
import socket
from urllib.parse import quote, urlparse
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .config import DATA_DIR, settings
from .live_shim import SHIM

try:
    import websockets
except Exception:                                    # pragma: no cover - optional at import time
    websockets = None                                # type: ignore

_LIVE_DIR = DATA_DIR / "live"

# project key -> {"target": id, "url": str, "device": str, "opened": ts, "seen": ts, "project": str}
_tabs: dict[str, dict] = {}
_lock = threading.Lock()

_DEVICES = {
    "desktop": {"width": 1280, "height": 720, "dpr": 1, "mobile": False, "touch": False},
    "phone": {"width": 390, "height": 844, "dpr": 3, "mobile": True, "touch": True},
    "tablet": {"width": 820, "height": 1180, "dpr": 2, "mobile": True, "touch": True},
}
_MOBILE_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Mobile Safari/537.36")

_NAV_SETTLE = 1.2          # seconds before we even ask the page what it is
# The heap query the bridge runs forces a major garbage collection, so it is the slowest thing in
# the open path by a wide margin. Bounded, because an engine it cannot find is not worth a hang.
_BRIDGE_BUDGET = 25.0
_MAX_VALUE = 120_000       # characters of one answer; past this an agent is reading noise


def _engine_pref(key: str, default=None):
    """One of the Studio engine settings (Settings → Studio engine): a nested dict under `engine`."""
    try:
        v = (settings.get("engine") or {}).get(key)
    except Exception:
        v = None
    return default if v is None else v


def enabled() -> bool:
    return bool(settings.get("cc_live", True))


def available() -> tuple[bool, str]:
    """Can this machine hold a live tab at all? Same browser, same answer as the review harness."""
    if websockets is None:
        return False, "the websockets package is missing"
    try:
        from . import review
        return review.available()
    except Exception as e:                           # pragma: no cover - defensive
        return False, str(e)


def key_for(project: str) -> str:
    try:
        return str(Path(project).resolve()).lower()
    except Exception:
        return str(project).lower()


def _slug(project: str) -> str:
    """A short readable folder name — the project's own name, plus enough of a hash to be unique."""
    import hashlib
    name = "".join(c if c.isalnum() or c in "-_" else "-" for c in Path(project).name)[:40]
    return "%s-%s" % (name or "project", hashlib.sha1(key_for(project).encode()).hexdigest()[:8])


# ---------------------------------------------------------------------------
# Where the game is served from
# ---------------------------------------------------------------------------
def _answers(url: str, timeout: float = 0.6) -> bool:
    """Is anything listening there? One TCP connect per address, all at once, no HTTP.

    ALL AT ONCE. `localhost` is two addresses, and Windows does not refuse a closed loopback port
    at once: it retries the SYN for 2 s. Tried in turn, a server on 127.0.0.1 behind a `localhost`
    URL cost the whole timeout while ::1 waited first - 0.614 s against 0.000 s, measured, and the
    forge asks this on every call."""
    try:
        p = urlparse(url if "://" in url else "http://" + url)
        port = p.port or (443 if p.scheme == "https" else 80)
        infos = socket.getaddrinfo(p.hostname or "127.0.0.1", port, type=socket.SOCK_STREAM)
    except (OSError, ValueError):
        return False
    waiting = (errno.EINPROGRESS, errno.EWOULDBLOCK, getattr(errno, "WSAEWOULDBLOCK", 10035))
    socks = []
    try:
        for fam, kind, proto, _name, addr in infos:
            try:
                s = socket.socket(fam, kind, proto)
            except OSError:
                continue
            socks.append(s)
            s.setblocking(False)
            err = s.connect_ex(addr)
            if err == 0:
                return True
            if err not in waiting:
                socks.remove(s)
                s.close()
        deadline = time.monotonic() + timeout
        while socks:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            _r, w, x = select.select([], socks, socks, left)
            for s in w:
                if s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0:
                    return True
            for s in set(w) | set(x):
                if s in socks:
                    socks.remove(s)
                    s.close()
        return False
    except OSError:
        return False
    finally:
        for s in socks:
            s.close()


def _origin_gone(e: dict) -> bool:
    """A remembered page URL whose server no longer answers.

    The forge keeps the URL it opened. A static server the reaper stopped, or a dev server a
    person closed, left every later call building on a dead origin: after the browser idled out
    the tab landed on Chrome's error page, and on the still-open tab a module fetch hung 45 s. A
    TCP connect tells the two apart: under a millisecond when the server is there, the 0.6 s
    timeout once when it is not."""
    url = str(e.get("url") or "")
    return url.startswith("http") and not _answers(url)


def origin_for(project: Path, start_dev: bool = True) -> tuple[str, str]:
    """A live session wants the project's own dev server, not a built snapshot.

    `dist/` is a photograph: the agent edits a file, the page does not change, and it concludes
    the edit did nothing. The dev server hot-reloads, so an edit shows up in the tab it is already
    looking at. Only when there is no dev script do we fall back to serving the folder.
    """
    from . import dev_server
    try:
        url = dev_server.running_url(project)
        # ASK THE PORT, DO NOT TAKE THE REGISTRY'S WORD. A dev server that has been closed since
        # it was registered still answers this question with a URL, and the page then navigates
        # to nothing and lands on chrome-error://. That is how a healthy-looking open produced a
        # blank tab and then a hang.
        if url and _answers(url):
            return url.rstrip("/"), "the project's running dev server"
    except Exception:
        pass
    if start_dev:
        try:
            if dev_server.script_for(project):
                # Bounded: an HTTP call an agent is waiting on must not sit for the module default.
                got = dev_server.start(project, timeout=25.0)
                if got.get("url"):
                    return str(got["url"]).rstrip("/"), "a dev server this call started"
        except Exception:
            pass
    from . import review
    return review.origin_for(project)


# ---------------------------------------------------------------------------
# One short connection per call
# ---------------------------------------------------------------------------
class _Live:
    """A CDP session bound to one live tab, for the length of one request."""

    def __init__(self, cdp, sid: str):
        self.cdp = cdp
        self.sid = sid

    async def call(self, method: str, params: Optional[dict] = None) -> dict:
        return await self.cdp.call(method, params or {}, self.sid)

    async def raw(self, expr: str, wait: bool = True) -> Any:
        return await self.cdp.js(expr, self.sid, wait=wait)

    async def ask(self, expr: str, depth: int = 6) -> Any:
        """Evaluate and bring the answer back as data, however tangled the value is.

        `returnByValue` on a live engine object either fails or returns a megabyte, so the page
        flattens it first with `__live.json` and hands over a string.

        The depth is a parameter because a scene dump is already bounded by its own tree depth and
        width — flattening it a second time at 6 turned the leaves of a depth-2 tree into the
        string "[Array]", which is not an answer.

        Wrapped in an async IIFE so an expression that returns a PROMISE is awaited. Without it
        `awaitPromise` only ever saw the finished string from `JSON.stringify`, so an async call
        like `__forge.ensure()` came back as a flattened Promise object and read as a failure.
        Awaiting a non-promise is free, so every caller can share one path.
        """
        out = await self.raw("(async()=>JSON.stringify(__live.json(await (%s), %d)))()"
                             % (expr, int(depth)))
        if not out:
            return None
        return json.loads(out)


async def _connect(ws_url: str):
    return await websockets.connect(ws_url, max_size=32 * 1024 * 1024)


async def _shim_present(live: _Live) -> bool:
    try:
        return bool(await live.raw("!!window.__live"))
    except Exception:
        return False


async def _install_shim(live: _Live, for_future_loads: bool = True) -> None:
    """Put the instrumentation in, and keep it in across reloads.

    Registering for new documents is what makes the shim see the FIRST error and the first frame,
    which is the whole reason it is worth having. Evaluating it now is what makes it work on the
    page that is already open — a reattach after a backend restart, say.
    """
    if for_future_loads:
        try:
            await live.call("Page.addScriptToEvaluateOnNewDocument", {"source": SHIM})
        except Exception:
            pass
    if not await _shim_present(live):
        await live.raw(SHIM, wait=False)


# Runs on the array queryObjects hands back, inside the page, and pins the first live instance.
_PICK = ("function (kind) { for (var i = 0; i < this.length; i++) { try { "
         "if (window.__live && window.__live.pin(this[i], kind)) return true; } catch (e) {} } "
         "return false; }")


async def _bridge(s: "_Live", entry: dict) -> dict:
    """When the page cannot find its own engine, find it in the heap instead.

    Phaser and PixiJS are the common case and they defeat the in-page hunt completely: the UMD
    build puts the CLASS on `window` (`Phaser`, `PIXI`) but the INSTANCE is a local inside the
    game's own closure, so nothing reachable from `window` points at it. No amount of walking the
    page's globals will ever reach it.

    The debugger can. `Runtime.queryObjects` returns every live object in the heap with a given
    prototype, and `Phaser.Game.prototype` IS reachable. Measured on a real Phaser 3.90 build:
    one instance, found in a few milliseconds. The instance is then pinned back into the page as
    `__live.pinned`, so every later call is an ordinary evaluate with no debugger involved.

    Once per tab, because the query forces a major garbage collection first — that is documented
    Chrome behaviour, not a surprise, and it is why this is not on the fast path.
    """
    reach = await s.ask("__live.reach()") or {}
    if reach.get("reachable") or entry.get("bridged"):
        return reach
    entry["bridged"] = True
    try:
        probes = await s.ask("__live.probes()") or []
    except Exception:
        return reach
    for probe in probes:
        try:
            kind, expr = probe[0], probe[1]
            r = await s.call("Runtime.evaluate", {"expression": expr, "returnByValue": False})
            oid = (r.get("result") or {}).get("objectId")
            if not oid:
                continue
            q = await s.call("Runtime.queryObjects", {"prototypeObjectId": oid})
            arr = (q.get("objects") or {}).get("objectId")
            if not arr:
                continue
            got = await s.call("Runtime.callFunctionOn", {
                "objectId": arr, "returnByValue": True,
                "arguments": [{"value": kind}], "functionDeclaration": _PICK})
            if (got.get("result") or {}).get("value"):
                return await s.ask("__live.reach()") or reach
        except Exception:
            continue
    # THE ROUTE THAT NEEDS NOTHING FROM THE PAGE. A bundled game exposes no prototype to query,
    # but it hands its frame callback to requestAnimationFrame every frame, and that closure holds
    # the engine — on ROT RUSH the callback's Module scope carries `pc`, and `pc.app` is the app.
    # The debugger reads closures; the page cannot. Bounded, once per tab, like the query above.
    try:
        from .live_navigate import hunt_closures
        kind = await asyncio.wait_for(hunt_closures(s), 9.0)
        if kind:
            await asyncio.sleep(0.1)          # a renderer-only find sees its scene on the next frame
            return await s.ask("__live.reach()") or reach
    except Exception:
        pass
    return reach


async def _open_target(cdp, url: str, dev: dict) -> str:
    # `newWindow`: a current Chrome refuses a size on a plain tab ("Target position can only be set
    # for new windows"). The one on this machine was old enough not to mind; a fresh PC's is not.
    t = await cdp.call("Target.createTarget",
                       {"url": "about:blank", "width": dev["width"], "height": dev["height"], "newWindow": True})
    return t["targetId"]


async def _attach(cdp, target_id: str) -> _Live:
    sid = (await cdp.call("Target.attachToTarget", {"targetId": target_id, "flatten": True}))["sessionId"]
    live = _Live(cdp, sid)
    await live.call("Page.enable", {})
    await live.call("Runtime.enable", {})
    return live


async def _apply_device(live: _Live, dev: dict) -> None:
    await live.call("Emulation.setDeviceMetricsOverride", {
        "width": dev["width"], "height": dev["height"],
        "deviceScaleFactor": dev["dpr"], "mobile": bool(dev["mobile"]),
    })
    try:
        await live.call("Emulation.setTouchEmulationEnabled",
                        {"enabled": bool(dev["touch"]), "maxTouchPoints": 5 if dev["touch"] else 1})
    except Exception:
        pass
    if dev["mobile"]:
        try:
            await live.call("Emulation.setUserAgentOverride", {"userAgent": _MOBILE_UA})
        except Exception:
            pass


async def _targets_alive(cdp) -> set:
    got = await cdp.call("Target.getTargets", {})
    return {t.get("targetId") for t in got.get("targetInfos", []) if t.get("type") == "page"}


def _device_of(name: str, width: int = 0, height: int = 0) -> dict:
    """The viewport to judge in. An explicit size wins; then a named device; then the shared one.

    `cc_engine_view` is what the engine window is set to, so an agent that names nothing looks at
    the game at the same resolution the user is looking at it. Judging a 1920x1080 game in a 900px
    pane is judging a different layout — the HUD reflows and text that fits stops fitting.
    """
    key = str(name or "").lower()
    if not key and not width and not height:
        try:
            view = settings.get("cc_engine_view") or {}
            w, h = int(view.get("w") or 0), int(view.get("h") or 0)
            if w and h:
                return {"width": max(200, min(3840, w)), "height": max(200, min(2160, h)),
                        "dpr": 1, "mobile": h > w, "touch": h > w}
        except Exception:
            pass
    dev = dict(_DEVICES.get(key or "desktop") or _DEVICES["desktop"])
    if width:
        dev["width"] = max(200, min(3840, int(width)))
    if height:
        dev["height"] = max(200, min(2160, int(height)))
    return dev


async def _boot(cdp, entry: dict, url: str, dev: dict, wait_ms: int) -> _Live:
    """Make a tab that is showing the game, whatever state we were in before."""
    target = await _open_target(cdp, url, dev)
    live = await _attach(cdp, target)
    await _apply_device(live, dev)
    await _install_shim(live)
    await live.call("Page.navigate", {"url": url})
    await asyncio.sleep(max(_NAV_SETTLE, wait_ms / 1000.0))
    entry["target"] = target
    entry["bridged"] = False        # a new document has a new heap; the old pin did not survive
    return live


async def _landed(live: "_Live", want: str) -> None:
    """Did the tab actually reach the page? Raise with the real reason if not.

    A URL nothing is serving does not fail loudly — Chrome quietly shows its own error page, and
    every step after that fails while naming something else: the shim cannot install on an error
    page (Chrome forbids it), so the next call says `__live is not defined`, and the one after
    that times out on `Runtime.evaluate`. All true, none of them the fault.
    """
    if not want:
        return
    try:
        here = str(await live.raw("location.href") or "")
    except Exception:                                        # noqa: BLE001
        return                                               # the deadline above owns this case
    if here.startswith("chrome-error:") or here.startswith("chrome://network-error"):
        raise RuntimeError(
            "nothing is serving %s — the tab is showing Chrome's own error page. Start the "
            "project's dev server, or pass a `url` that is up." % want)


async def _session(entry: dict, want_url: str = "", wait_ms: int = 0) -> tuple[Any, _Live]:
    """Attach to this project's tab, or make one. Self-healing on purpose.

    The browser is reaped after ten idle minutes, and a user can close it from the pill. An agent
    that calls `/eval` an hour later should get its answer, not a lecture about a dead tab.
    """
    from . import review
    ws_url = review._ensure_browser()
    ws = await _connect(ws_url)
    cdp = review._Cdp(ws)
    dev = _device_of(entry.get("device") or "desktop",
                     entry.get("width") or 0, entry.get("height") or 0)
    url = want_url or entry.get("url") or "about:blank"
    try:
        target = entry.get("target") or ""
        if target and target in await _targets_alive(cdp):
            live = await _attach(cdp, target)
            if not await _shim_present(live):
                await _install_shim(live)
                # No shim on a tab we opened means the GAME reloaded it (location.reload, a Vite
                # full reload), and the new document has a new heap: the old pin is gone with the
                # old one. Left marked as bridged, the heap query never ran again and every later
                # call said "unreachable" on a page that was simply new.
                entry["bridged"] = False
            if want_url and want_url != entry.get("url"):
                # The new document gets the shim at its start, as a new tab does: registered on
                # THIS session, because a registration dies with the session that made it.
                try:
                    await live.call("Page.addScriptToEvaluateOnNewDocument", {"source": SHIM})
                except Exception:
                    pass
                await live.call("Page.navigate", {"url": want_url})
                await asyncio.sleep(max(_NAV_SETTLE, wait_ms / 1000.0))
                await _landed(live, want_url)
                entry["url"] = want_url
                entry["bridged"] = False        # a new document has a new heap
                if not await _shim_present(live):
                    await _install_shim(live, for_future_loads=False)
            return ws, live
        live = await _boot(cdp, entry, url, dev, wait_ms or 1500)
        await _landed(live, url if url != "about:blank" else "")
        entry["url"] = url
        return ws, live
    except Exception:
        await ws.close()
        raise


async def _reopen(entry: dict, url: str, dev: dict, wait_ms: int, new_window: bool) -> tuple[Any, _Live]:
    """open {reload:true}: a fresh document of `url` in THIS project's tab.

    IN PLACE whenever it can be: the same tab reloaded (or sent to the other page). A new tab for
    every reload leaked one — the old target was dropped from the record and never closed, and a
    headless tab keeps running its game loop at full speed (vsync is off) until the browser idles
    out: ten edit-and-reload rounds were ten copies of the game in the Chrome every agent shares.
    The same tab also keeps what is attached to it: the person's live stream (live_stream.py)
    follows a target id, and a reload in place never changes it.

    A NEW tab only when the window itself must change (another device or size: the window is made
    at that size, and an emulation set on a session ends with the session), or when the reload in
    place failed (a page stuck in a loop never finishes reloading). The new tab is made first and
    the old one closed after, so the record never points at nothing and nothing is left behind."""
    from . import review
    ws = await _connect(review._ensure_browser())
    cdp = review._Cdp(ws)
    try:
        old = entry.get("target") or ""
        alive = bool(old) and old in await _targets_alive(cdp)
        why = ""
        if alive and not new_window:
            t0 = time.monotonic()
            live = await _attach(cdp, old)
            try:
                if url != entry.get("url"):
                    await live.call("Page.addScriptToEvaluateOnNewDocument", {"source": SHIM})
                    await live.call("Page.navigate", {"url": url})
                    await asyncio.sleep(max(_NAV_SETTLE, wait_ms / 1000.0))
                    await _landed(live, url)
                    entry["url"] = url
                    entry["bridged"] = False
                    if not await _shim_present(live):
                        await _install_shim(live, for_future_loads=False)
                else:
                    await _reload_tab(live, entry, sidecar=False)
                    # The same patience a new tab is given: `wait_ms` is how long the game needs.
                    rest = wait_ms / 1000.0 - (time.monotonic() - t0)
                    if rest > 0:
                        await asyncio.sleep(rest)
                entry["reloaded"] = "in place"
                return ws, live
            except Exception as ex:                          # noqa: BLE001
                if "nothing is serving" in str(ex):
                    raise                                    # a new tab would land on the same error page
                why = str(ex)[:160]
        live = await _boot(cdp, entry, url, dev, wait_ms or 1500)
        if alive and old != entry.get("target"):
            try:
                await cdp.call("Target.closeTarget", {"targetId": old})
            except Exception:                                # noqa: BLE001
                pass
        await _landed(live, url if url != "about:blank" else "")
        entry["url"] = url
        entry["reloaded"] = ("a new tab: the reload in place failed (%s)" % why) if why else (
            "a new tab: the device or size changed" if alive else "a new tab")
        return ws, live
    except Exception:
        await ws.close()
        raise


def _run(fn):
    """Run one coroutine that needs a live session, and always close the socket."""
    return asyncio.run(fn())


def _entry(project: str, create: bool = True) -> Optional[dict]:
    k = key_for(project)
    with _lock:
        e = _tabs.get(k)
        if e is None and create:
            e = {"project": str(Path(project)), "target": "", "url": "", "device": "desktop",
                 "width": 0, "height": 0, "opened": time.time(), "seen": time.time(), "how": ""}
            _tabs[k] = e
        if e is not None:
            e["seen"] = time.time()
        return e


def _guard(project: str, forge: bool = False) -> Optional[dict]:
    """The two refusals worth making before anything touches a browser.

    The forge has a switch of its own (cc_forge): it is how an asset gets MADE, and a person may
    want it with the live link off — or, the other way round, want the forge gone entirely, in
    which case the agent is not told about it and this refuses as well."""
    if forge:
        if not settings.get("cc_forge", True):
            return {"ok": False, "error": "The forge is off. Turn it on in Settings → Studio engine → Forge."}
    elif not enabled():
        return {"ok": False, "error": "Live game link is off. Turn it on in Settings → Planning."}
    ok, why = available()
    if not ok:
        return {"ok": False, "error": why}
    if not project or not Path(project).exists():
        return {"ok": False, "error": f"no such folder: {project}"}
    return None


# ---------------------------------------------------------------------------
# The operations
# ---------------------------------------------------------------------------
def open_(project: str, url: str = "", device: str = "", width: int = 0, height: int = 0,
          reload: bool = False, start_dev: bool = True, wait_ms: int = 1500) -> dict:
    bad = _guard(project)
    if bad:
        return bad
    root = Path(project)
    how = ""
    if not url:
        try:
            origin, how = origin_for(root, start_dev=start_dev)
        except Exception as e:
            return {"ok": False, "error": f"could not find a way to serve the project: {e}"}
        url = origin.rstrip("/") + "/"
    e = _entry(project)
    was = (str(e.get("device") or "desktop"), int(e.get("width") or 0), int(e.get("height") or 0))
    if device:
        e["device"] = device
    if width:
        e["width"] = width
    if height:
        e["height"] = height
    if how:
        e["how"] = how
    now = (str(e.get("device") or "desktop"), int(e.get("width") or 0), int(e.get("height") or 0))
    e.pop("reloaded", None)

    async def go():
        if reload:
            ws, live = await _reopen(e, url, _device_of(*now), wait_ms, new_window=(now != was))
        else:
            ws, live = await _session(e, want_url=url, wait_ms=wait_ms)
        try:
            # THE BRIDGE MUST NOT BE ABLE TO FAIL THE OPEN. It is an optimisation for Phaser and
            # PixiJS, whose instances are unreachable from `window`; when it cannot find one the
            # page is still open and still usable. It used to be able to hang the whole request.
            try:
                await asyncio.wait_for(_bridge(live, e), _BRIDGE_BUDGET)
            except Exception as _bx:                         # noqa: BLE001
                e["bridged"] = True                          # do not pay for it again
                e["bridge_note"] = str(_bx)[:200]
            # The editor's sidecar, when the project has one: the shim applies it to every three.js
            # scene it finds, now and as they appear, so a game opened live carries the edits made
            # in the Edit tab without a line of code. (Named by URL; the shim fetches it itself.)
            try:
                sidecar = root / "studio.edits.json"
                if sidecar.is_file() and _engine_pref("live_sidecar", True):
                    # THIS STUDIO'S OWN ORIGIN, not a guess. Hard-coding 8777 handed the page
                    # a URL pointing at whichever Studio happened to own that port — which on a
                    # machine running a second backend is a different Studio entirely.
                    sc_url = _sidecar_url(root)
                    await live.raw("__live.sidecar && __live.sidecar(%s)" % json.dumps(sc_url), wait=False)
            except Exception:
                pass
            return await live.ask("__live.ready()")
        finally:
            await ws.close()

    try:
        ready = _run(go) or {}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}
    e["url"] = url
    e["engine"] = ready.get("engine") or "?"
    out = {"ok": True, "url": url, "served_by": e.get("how") or "",
           "device": e.get("device") or "desktop", **ready}
    if reload and e.get("reloaded"):
        out["reloaded"] = e.pop("reloaded")
    return out


def evaluate(project: str, js: str, depth: int = 6) -> dict:
    """Run an expression, or a block of statements, and bring the value back.

    Expression first, statements second, because `pc.app.scene.ambientLight` is what an agent
    types nine times out of ten and wrapping it in a function body would make it return nothing.
    A block only has to say `return` if it wants an answer.
    """
    bad = _guard(project)
    if bad:
        return bad
    if not str(js).strip():
        return {"ok": False, "error": "nothing to run"}
    e = _entry(project)
    d = max(1, min(12, int(depth or 6)))
    # One round trip carries both answers, and the console half is read back from a MARK rather
    # than drained. Draining here was the first version, and it quietly emptied /api/live/console
    # forever: every eval swallowed the records before the endpoint that owns them could report.
    env = ("(async()=>{const m=__live.mark();const v=await (%s);"
           "return JSON.stringify({v:__live.cap(v,%d,%d),c:__live.since(m)});})()")
    as_expr = env % (js, d, _MAX_VALUE)
    as_body = env % ("(async()=>{%s})()" % js, d, _MAX_VALUE)

    async def go():
        ws, live = await _session(e)
        try:
            try:
                return await live.raw(as_expr), "expression"
            except Exception as first:
                if "SyntaxError" not in str(first):
                    raise
                return await live.raw(as_body), "statements"
        finally:
            await ws.close()

    try:
        out, form = _run(go)
    except Exception as ex:
        return {"ok": False, "error": str(ex).strip()[:1200]}
    try:
        env_out = json.loads(out) if out else {}
    except Exception:
        return {"ok": False, "error": "the page returned something that is not JSON",
                "raw": str(out)[:2000]}
    value = env_out.get("v")
    res: dict = {"ok": True, "value": value, "form": form}
    if isinstance(value, dict) and value.get("__truncated"):
        res["note"] = ("the answer was %d chars and the limit is %d — ask for one field, not the "
                       "whole object" % (value["__truncated"], value.get("__limit", _MAX_VALUE)))
    errs = env_out.get("c") or []
    if errs:
        res["console"] = errs[:40]
    return res


def scene(project: str, depth: int = 3, wide: int = 40, root: str = "", index: int = 0) -> dict:
    bad = _guard(project)
    if bad:
        return bad
    e = _entry(project)
    opts = json.dumps({"depth": int(depth), "wide": int(wide),
                       "root": root or None, "index": int(index)})

    async def go():
        ws, live = await _session(e)
        try:
            await _bridge(live, e)
            # 14, not the default: the tree is already bounded by `depth` and `wide`, so the only
            # thing a low serialisation depth can do here is hide the leaves.
            got = await live.ask("__live.scene(%s)" % opts, depth=14) or {}
            try:
                if isinstance(got, dict) and await live.raw(
                        "!!(window.__forge && window.__forge.ready && window.__forge.ready().built)"):
                    got["note"] = ("this page also holds a forge bench; this tree is the page's own "
                                   "scene, not the bench - /api/live/look with numbers:true (or "
                                   "/api/live/bench) says what the bench holds")
            except Exception:
                pass
            return got
        finally:
            await ws.close()

    try:
        return {"ok": True, **(_run(go) or {})}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}


def find(project: str, query: str, limit: int = 25, code: bool = False) -> dict:
    """Where the named things are. `code` adds, to each match, the lines of the project's own
    source that most likely made it (see code_hints) - the answer to "which file do I edit"."""
    bad = _guard(project)
    if bad:
        return bad
    e = _entry(project)

    async def go():
        ws, live = await _session(e)
        try:
            await _bridge(live, e)
            return await live.ask("__live.find(%s,%d)" % (json.dumps(str(query)), int(limit)))
        finally:
            await ws.close()

    try:
        res = {"ok": True, **(_run(go) or {})}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}
    if code and isinstance(res.get("matches"), list) and res["matches"]:
        _code_for(project, res["matches"], e)
    return res


# WHICH GAME THE TAB IS SERVING, inside a workspace that holds several. The brainrot workspace
# keeps rot-rush and rot-haul side by side, and they reuse each other's names: `'camera'` is named
# on line 114 of one and line 662 of the other. A hint from the game next door sends an agent to
# edit a file the running game never loads. Answered from the port: a static server this Studio
# started knows its folder, anything else is the working directory of the process listening there.
_SERVING: dict = {}


def _serving_root(project: str, url: str) -> str:
    """The folder the tab's server serves, relative to `project`, or "" when it is the project."""
    try:
        port = urlparse(url).port if url else None
    except ValueError:
        port = None
    if not port:
        return ""
    k = (key_for(project), int(port))
    hit = _SERVING.get(k)
    if hit and time.time() - hit[0] < 60.0:
        return hit[1]
    folder = ""
    try:
        from . import preview_server
        for f, rec in list(preview_server._servers.items()):
            if int(rec.get("port") or 0) == int(port):
                folder = f
                break
    except Exception:
        folder = ""
    if not folder:
        try:
            import psutil
            for c in psutil.net_connections(kind="inet"):
                if c.status == "LISTEN" and c.laddr and int(c.laddr.port) == int(port) and c.pid:
                    folder = psutil.Process(c.pid).cwd() or ""
                    break
        except Exception:
            folder = ""
    rel = ""
    if folder:
        try:
            rel = Path(folder).resolve().relative_to(Path(project).resolve()).as_posix()
            rel = "" if rel == "." else rel
        except (ValueError, OSError):
            rel = ""
    _SERVING[k] = (time.time(), rel)
    return rel


def _code_for(project: str, rows: list, e: Optional[dict] = None, per_name: int = 3) -> None:
    """Put `code` on each row: up to three `"<file>:<line>: <source>"` lines for its name."""
    try:
        from .code_hints import hints_for
    except Exception:
        return
    names = [str(r.get("name") or "") for r in rows if isinstance(r, dict)]
    try:
        got = hints_for(project, names, per_name,
                        prefer=_serving_root(project, str((e or {}).get("url") or "")))
    except Exception as ex:                                  # noqa: BLE001
        got = {}
        for r in rows:
            if isinstance(r, dict):
                r["code_error"] = str(ex)[:200]
    for r in rows:
        if isinstance(r, dict):
            r["code"] = got.get(str(r.get("name") or ""), [])


def console(project: str, level: str = "all", drain: bool = True) -> dict:
    """What the page said. Draining by default, so two calls do not report the same error twice."""
    bad = _guard(project)
    if bad:
        return bad
    e = _entry(project)

    async def go():
        ws, live = await _session(e)
        try:
            return await live.ask("__live.%s()" % ("drain" if drain else "peek"))
        finally:
            await ws.close()

    try:
        rows = _run(go) or []
    except Exception as ex:
        return {"ok": False, "error": str(ex)}
    # A row is a dict from the shim, but a page can push anything onto that queue — a bare string
    # arrives as itself — and one such row turned the whole endpoint into a 500 for the agent that
    # most needed to read the console at that moment.
    rows = [r if isinstance(r, dict) else {"kind": "log", "text": str(r)[:2000]} for r in rows]
    lvl = str(level or "all").lower()
    if lvl in ("warn", "error", "bad"):
        keep = {"error", "warn", "promise", "resource", "net"} if lvl != "error" \
            else {"error", "promise", "resource"}
        rows = [r for r in rows if r.get("kind") in keep]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("kind", "?")] = counts.get(r.get("kind", "?"), 0) + 1
    return {"ok": True, "counts": counts, "records": rows[:200],
            "more": max(0, len(rows) - 200)}


def perf(project: str, ms: int = 1500) -> dict:
    """What a frame of this game COSTS. Read `frame_ms_median`, not `fps`.

    The review harness drives the clock by hand so every run matches, which is exactly why it can
    never report timing: that clock is ours. Here the clock is real — but headless Chrome does not
    wait for a monitor, so `requestAnimationFrame` runs as fast as the work allows and the fixture
    here measured 360fps. That number is a CEILING, not what a player sees.

    The useful number survives that: 16.7ms is the whole 60fps budget, and a frame that costs 2.8ms
    of work has room while one that costs 24ms cannot hit 60 anywhere. So `budget_60_pct` is
    reported beside the raw figures, and the ceiling is labelled as one.
    """
    bad = _guard(project)
    if bad:
        return bad
    e = _entry(project)
    span = max(200, min(20_000, int(ms or 1500)))

    async def go():
        ws, live = await _session(e)
        try:
            await live.raw("__live.resetPerf()")
            await asyncio.sleep(span / 1000.0)
            return await live.ask("__live.perf()")
        finally:
            await ws.close()

    try:
        got = _run(go) or {}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}
    out = {"ok": True, "window_ms": span, "vsync": False, **got}
    med = float(got.get("frame_ms_median") or 0)
    p95 = float(got.get("frame_ms_p95") or 0)
    if med > 0:
        out["budget_60_pct"] = round(med / 16.67 * 100, 1)
        out["budget_60_pct_p95"] = round(p95 / 16.67 * 100, 1)
    if got.get("hidden"):
        out["note"] = ("the tab was backgrounded, so Chrome throttled rAF — these numbers are the "
                       "throttle, not the game")
    elif got.get("frames", 0) < 5:
        out["note"] = "almost no frames in the window: this page may not animate at all"
    elif med > 16.67:
        out["note"] = ("a frame costs %.1fms and the 60fps budget is 16.7ms — this cannot hit 60 "
                       "on this machine. `fps` is a headless ceiling; read frame_ms." % med)
    else:
        out["note"] = ("a frame costs %.1fms of the 16.7ms 60fps budget (%.0f%%). `fps` is a "
                       "headless ceiling — there is no monitor to wait for — so judge cost, not "
                       "that number." % (med, out.get("budget_60_pct", 0)))
    return out


# ---------------------------------------------------------------------------
# Driving it
# ---------------------------------------------------------------------------
def _pair(ev: dict, name: str, dev: dict) -> tuple[float, float]:
    """Pixels by default; `n`-prefixed keys are fractions of the viewport.

    Guessing "a value under 1 must be a fraction" was the first version, and it makes x=1 mean
    two different things. Two names, one rule each: `x`/`y` are pixels, `nx`/`ny` are 0..1.
    """
    W, H = float(dev["width"]), float(dev["height"])
    if name == "point":
        if "nx" in ev or "ny" in ev:
            return float(ev.get("nx", 0)) * W, float(ev.get("ny", 0)) * H
        return float(ev.get("x", 0) or 0), float(ev.get("y", 0) or 0)
    npt = ev.get("n" + name)
    if isinstance(npt, (list, tuple)) and len(npt) >= 2:
        return float(npt[0]) * W, float(npt[1]) * H
    pt = ev.get(name)
    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
        return float(pt[0]), float(pt[1])
    return _pair(ev, "point", dev)


async def _one_input(live: _Live, ev: dict, dev: dict) -> None:
    kind = str(ev.get("type") or "").lower()
    W, H = dev["width"], dev["height"]
    x, y = _pair(ev, "point", dev)

    if kind == "wait":
        await asyncio.sleep(max(0.0, min(10.0, float(ev.get("ms", 100)) / 1000.0)))
        return
    if kind == "js":
        await live.raw(str(ev.get("js") or ""), wait=False)
        return
    if kind in ("key", "keydown", "keyup", "press"):
        k = str(ev.get("key") or "")
        code = str(ev.get("code") or "")
        vk = int(ev.get("keyCode") or 0)
        base = {"key": k, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
        down = dict(base, type="keyDown")
        if len(k) == 1:
            down["text"] = k
        if kind != "keyup":
            await live.call("Input.dispatchKeyEvent", down)
        if kind in ("key", "press", "keyup"):
            hold = float(ev.get("ms", 60)) / 1000.0
            if kind != "keyup" and hold > 0:
                await asyncio.sleep(min(2.0, hold))
            await live.call("Input.dispatchKeyEvent", dict(base, type="keyUp"))
        return
    if kind == "text":
        await live.call("Input.insertText", {"text": str(ev.get("text") or "")})
        return
    if kind in ("move", "mousemove"):
        await live.call("Input.dispatchMouseEvent",
                        {"type": "mouseMoved", "x": x, "y": y, "button": "none"})
        return
    if kind in ("click", "mouse", "down", "up"):
        btn = str(ev.get("button") or "left")
        base = {"x": x, "y": y, "button": btn, "clickCount": 1, "buttons": 1}
        await live.call("Input.dispatchMouseEvent", {**base, "type": "mouseMoved", "buttons": 0})
        if kind != "up":
            await live.call("Input.dispatchMouseEvent", {**base, "type": "mousePressed"})
        if kind in ("click", "mouse", "up"):
            await asyncio.sleep(min(2.0, float(ev.get("ms", 40)) / 1000.0))
            await live.call("Input.dispatchMouseEvent", {**base, "type": "mouseReleased", "buttons": 0})
        return
    if kind == "wheel":
        await live.call("Input.dispatchMouseEvent",
                        {"type": "mouseWheel", "x": x, "y": y, "button": "none",
                         "deltaX": float(ev.get("dx", 0)), "deltaY": float(ev.get("dy", -120))})
        return
    if kind in ("tap", "touch"):
        pt = [{"x": x, "y": y, "id": 1, "radiusX": 4, "radiusY": 4, "force": 1}]
        await live.call("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": pt})
        await asyncio.sleep(min(2.0, float(ev.get("ms", 60)) / 1000.0))
        await live.call("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        return
    if kind == "swipe":
        ax, ay = _pair(ev, "from", dev)
        bx, by = _pair(ev, "to", dev)
        steps = max(2, min(40, int(ev.get("steps", 12))))
        total = max(0.05, min(5.0, float(ev.get("ms", 300)) / 1000.0))
        pt = lambda X, Y: [{"x": X, "y": Y, "id": 1, "radiusX": 4, "radiusY": 4, "force": 1}]
        await live.call("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": pt(ax, ay)})
        for i in range(1, steps + 1):
            f = i / steps
            await live.call("Input.dispatchTouchEvent",
                            {"type": "touchMove",
                             "touchPoints": pt(ax + (bx - ax) * f, ay + (by - ay) * f)})
            await asyncio.sleep(total / steps)
        await live.call("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        return
    raise ValueError("unknown input type %r" % kind)


def send_input(project: str, events: list) -> dict:
    """Keys, mouse, wheel, touch taps and swipes — in the order given, with waits between.

    Touch is here because half of this user's game is a phone layout, and a mouse click is not a
    touch: a game that listens for `touchstart` never hears `mousedown`.
    """
    bad = _guard(project)
    if bad:
        return bad
    evs = [e for e in (events or []) if isinstance(e, dict)]
    if not evs:
        return {"ok": False, "error": "no events"}
    if len(evs) > 200:
        return {"ok": False, "error": "at most 200 events in one call"}
    e = _entry(project)
    dev = _device_of(e.get("device") or "desktop", e.get("width") or 0, e.get("height") or 0)

    async def go():
        ws, live = await _session(e)
        try:
            mark = await live.raw("__live.mark()")
            done = 0
            for ev in evs:
                await _one_input(live, ev, dev)
                done += 1
            # What THIS input caused, and nothing older. Not a drain: /api/live/console owns that.
            errs = await live.ask("__live.since(%d)" % int(mark or 0)) or []
            return done, errs
        finally:
            await ws.close()

    try:
        done, errs = _run(go)
    except Exception as ex:
        return {"ok": False, "error": str(ex)}
    out = {"ok": True, "sent": done}
    if errs:
        out["console"] = errs[:40]
    return out


def shot(project: str, path: str = "", full: bool = False) -> dict:
    """One frame, right now. Not a substitute for the review harness — a way to look after a poke."""
    bad = _guard(project)
    if bad:
        return bad
    e = _entry(project)
    out = Path(path) if path else (_LIVE_DIR / _slug(project) / ("shot-%d.png" % int(time.time())))

    async def go():
        ws, live = await _session(e)
        try:
            params: dict = {"format": "png"}
            if full:
                params["captureBeyondViewport"] = True
            got = await live.call("Page.captureScreenshot", params)
            return got.get("data") or ""
        finally:
            await ws.close()

    try:
        data = _run(go)
    except Exception as ex:
        return {"ok": False, "error": str(ex)}
    if not data:
        return {"ok": False, "error": "the browser returned no image"}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(data))
    return {"ok": True, "path": str(out), "bytes": out.stat().st_size}


# ---------------------------------------------------------------------------
# The forge — write asset code, see the asset
# ---------------------------------------------------------------------------
_VIEWS = ("3q", "front", "back", "side", "left", "top", "bottom", "low", "hero", "back3q")
# A PRESET IS A SHORTCUT, NOT THE SET OF ALLOWED ANGLES. Ten names cannot contain the angle a
# particular photograph was taken from, and an agent asked to match a picture needs exactly that
# angle. So a view is a name OR a spec: `az=35`, `az=35,el=12`, `az=35,el=12,zoom=2`. Azimuth 0
# looks at the subject's front (+Z), 90 at its LEFT side - glTF's convention: the subject faces +Z
# and its own left is +X, where az=90 stands - 180 at its back, 270 at its right side. So the
# preset `side` (+X) shows its left side and `left` (-X, your left as you face it) its right side.
# Elevation lifts the camera; zoom divides the framing margin, so zoom=3 is a close-up of the whole
# subject - and a written zoom is taken as a camera, never refitted (see dirOf in live_forge.py).
_SPEC_RE = re.compile(r"^\s*(?:(?:az|azimuth|el|elev|elevation|zoom|z)\s*=\s*-?\d+(?:\.\d+)?"
                      r"\s*[,;\s]*)+$", re.I)


def _view_ok(v: str) -> str:
    """The view string to send to the page, or '' if it is neither a preset nor a spec.

    A preset may carry modifiers - "top,zoom=2.5", "side,el=20" - and the page starts from the
    preset's own az and el. That form used to be dropped here without a word, so a close top view
    an agent asked for was simply missing from its sheet."""
    t = str(v or "").strip()
    if t in _VIEWS:
        return t
    if "=" in t and len(t) <= 60:
        head, _, rest = t.partition(",")
        if head.strip() in _VIEWS and rest and _SPEC_RE.match(rest):
            return (head.strip() + "," + rest).replace(" ", "")
        if _SPEC_RE.match(t):
            return t.replace(" ", "")
    return ""


def _view_notes(asked) -> list:
    """Name the views that could not be read, instead of quietly drawing fewer panels."""
    bad = [str(v) for v in (asked or []) if str(v).strip() and not _view_ok(v)]
    if not bad:
        return []
    return ["VIEWS: dropped %s - a view is a preset (%s), az=/el=/zoom= numbers, or a preset with "
            "modifiers such as top,zoom=2." % (", ".join(repr(b) for b in bad[:6]), " ".join(_VIEWS))]
# `faces` and `matcap` are Blender's two most useful overlays, and both answer a question a lit
# render cannot. `faces` shows an inverted winding as a red surface — an asset here shipped built
# inside out and it took a signed-volume check in Node to find. `matcap` drops colour and lighting
# so what is left is form, which is what a silhouette does in two dimensions.
_PASSES = ("silhouette", "wireframe", "normals", "faces", "matcap")
# A sheet is read by a vision model at a fixed budget. Past a dozen panels each one is too small
# to judge, so the picture stops being evidence and becomes a mosaic.
_MAX_PANELS = 12


def _label_params(params: dict, i: int) -> str:
    """A caption a variant can be told apart by: only the values, shortest first."""
    if not isinstance(params, dict) or not params:
        return "#%d" % (i + 1)
    bits = []
    for k, v in list(params.items())[:3]:
        if isinstance(v, float):
            v = round(v, 4)
        bits.append("%s=%s" % (k, v))
    return " ".join(bits)[:38]


# The target picture, remembered per project.
#
# "Check the result against the reference" is not a step an agent should have to remember, and
# comparing a render on screen against an image it read twenty tool calls ago is comparing against
# a memory. So the reference is composited INTO the sheet, first panel, every run -- and the path
# is kept, so it is passed once and then it is simply always there.
_REF_MAX = 4_000_000            # bytes; a reference is a picture, not a texture atlas


def _ref_file(project: str):
    return _LIVE_DIR / _slug(project) / "reference.txt"


def _crop_box(spec, img_path):
    """[x0, y0, x1, y1] in pixels, from fractions or from pixels.

    A turnaround reference is a SHEET: six figures and their labels on one plate. Segmentation
    answers "the biggest thing here", which on a sheet is a band across two figures, so an agent
    had to write crop.py before any number meant anything. Four numbers say which tile."""
    from PIL import Image
    try:
        parts = [float(x) for x in str(spec).replace(";", ",").split(",")]
    except ValueError:
        return None
    if len(parts) != 4:
        return None
    with Image.open(img_path) as im:
        W, H = im.size
    if max(parts) <= 1.0:
        parts = [parts[0] * W, parts[1] * H, parts[2] * W, parts[3] * H]
    x0, y0, x1, y1 = [int(round(v)) for v in parts]
    x0, y0 = max(0, min(W - 2, x0)), max(0, min(H - 2, y0))
    x1, y1 = max(x0 + 2, min(W, x1)), max(y0 + 2, min(H, y1))
    return (x0, y0, x1, y1)


def reference(project: str, path: str = "", mask: str = "", crop: str = "") -> dict:
    """Set or read this project's reference image, and the mask that says where its subject is.

    Two lines in one file: the picture, then an optional mask (white where the subject is). The
    mask exists because segmentation by colour alone cannot find near-black jeans on a near-black
    plate; `_mask_of_photo` now reads edges too, and a supplied mask beats any guess. Pass
    `mask="-"` to forget it."""
    f = _ref_file(project)
    try:
        rows = [r.strip() for r in f.read_text(encoding="utf-8").splitlines()]
    except OSError:
        rows = []
    img = rows[0] if rows else ""
    msk = rows[1] if len(rows) > 1 else ""
    src = rows[2] if len(rows) > 2 else ""      # the sheet a crop was cut from
    if path:
        p = Path(path)
        if not p.is_file():
            return {"ok": False, "error": "no such image: %s" % path}
        img, src = str(p.resolve()), ""         # a new picture forgets the old crop
    if mask:
        if str(mask).strip() == "-":
            msk = ""
        else:
            q = Path(mask)
            if not q.is_file():
                return {"ok": False, "error": "no such mask: %s" % mask}
            msk = str(q.resolve())
    if crop:
        base = src or img
        if str(crop).strip() == "-":
            if src:
                img, src = src, ""              # the whole sheet again
        elif base and Path(base).is_file():
            box = _crop_box(crop, base)
            if not box:
                return {"ok": False, "error": "a crop is four numbers: x0,y0,x1,y1 "
                                              "(0..1 of the picture, or pixels)"}
            from PIL import Image
            out = _LIVE_DIR / _slug(project) / "reference-crop.png"
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
                with Image.open(base) as im:
                    im.convert("RGB").crop(box).save(out)
            except Exception as ex:
                return {"ok": False, "error": "could not cut the crop: %s" % str(ex)[:160]}
            src, img = base, str(out)
    if path or mask or crop:
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("\n".join([img, msk, src]).rstrip(), encoding="utf-8")
        except OSError as e:
            return {"ok": False, "error": str(e)}
    return {"ok": True,
            "reference": img if img and Path(img).is_file() else "",
            "mask": msk if msk and Path(msk).is_file() else "",
            "source": src if src and Path(src).is_file() else "",
            "remembered": bool(path or mask or crop)}


# The words that mean "not this time". Anything else is a path, or "" for the sticky one.
_REF_OFF = {"none", "off", "no", "false", "0"}
# `"ref_mask": "auto"` is the word an agent reaches for, and it used to be read as a path to a
# PNG that does not exist - which switched the mask OFF, the opposite of what it asks for. The
# threshold ladder IS the automatic mask; naming it costs one set.
_MASK_AUTO = {"auto", "default", "on", "true", "1", "yes"}


def _ref_off(ref) -> bool:
    return str(ref or "").strip().lower() in _REF_OFF


def _mask_auto(mask) -> bool:
    """True when the caller asked for the automatic mask rather than naming a PNG."""
    return str(mask or "").strip().lower() in _MASK_AUTO


def ref_facts(path: str, mask_path: str = "") -> dict:
    """What the TARGET picture is, as numbers: where it sits, how wide it is, how dark it gets.

    Everything here comes off the mask the score already builds, so it costs a cache lookup. It
    exists because thirteen of one agent's eighteen throwaway scripts were measuring exactly
    this — the reference is the one thing in the loop nothing ever described.
    """
    import numpy as np
    from PIL import Image as _Im
    try:
        m = _mask_of_reference(path, mask_path)
    except Exception as ex:
        return {"error": str(ex)[:200]}
    if m is None or not m.any():
        return {"error": "no subject could be separated from the backdrop"}
    h, w = m.shape
    ys, xs = np.nonzero(m)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    rows = m.sum(axis=1)
    # SIXTEEN BANDS, top to bottom, each the widest span of subject in that band as a share of the
    # subject's own width. This is the shape a silhouette score cannot tell you: 0.31 at the
    # shoulders and 0.12 at the waist is a description you can build against.
    bands = []
    span = max(1, x1 - x0 + 1)
    for i in range(16):
        a, b = y0 + (y1 - y0 + 1) * i // 16, y0 + (y1 - y0 + 1) * (i + 1) // 16
        b = max(a + 1, b)
        seg = m[a:b]
        cols = np.nonzero(seg.any(axis=0))[0]
        bands.append(round(float((cols.max() - cols.min() + 1) / span), 3) if cols.size else 0.0)
    out = {
        "size": [int(w), int(h)],
        # 0..1 of the whole picture: left, top, right, bottom.
        "bbox": [round(x0 / w, 3), round(y0 / h, 3), round((x1 + 1) / w, 3), round((y1 + 1) / h, 3)],
        "fills": round(float(m.mean()), 4),
        "aspect": round(float((x1 - x0 + 1) / max(1, y1 - y0 + 1)), 3),
        "width_by_height": bands,
    }
    try:
        im = _Im.open(path).convert("RGB")
        if im.size != (w, h):
            im = im.resize((w, h), _Im.LANCZOS)
        a = np.asarray(im, dtype=np.float32)
        lum = (0.2126 * a[:, :, 0] + 0.7152 * a[:, :, 1] + 0.0722 * a[:, :, 2])[m]
        out["tone"] = {"p2": round(float(np.percentile(lum, 2)), 1),
                       "p25": round(float(np.percentile(lum, 25)), 1),
                       "median": round(float(np.median(lum)), 1),
                       "p75": round(float(np.percentile(lum, 75)), 1),
                       "p98": round(float(np.percentile(lum, 98)), 1),
                       # The share that is genuinely near-black. A jacket the reference reads as
                       # black and the build reads as mid-grey differ HERE and nowhere else.
                       "near_black": round(float((lum < 40).mean()), 3)}
        rgb = a[m]
        out["colour"] = [int(round(float(np.median(rgb[:, i])))) for i in range(3)]
    except Exception:
        pass
    return out


def _ref_frame(project: str, ref: str, size):
    """The reference, letterboxed to the render's own panel size so the sheet lays out evenly."""
    path = ""
    if ref:
        got = reference(project, ref)
        if not got.get("ok"):
            return None, got.get("error", "")
        path = got.get("reference") or ""
    else:
        path = reference(project).get("reference") or ""
    if not path:
        return None, ""
    try:
        if Path(path).stat().st_size > _REF_MAX:
            return None, "reference is larger than %d bytes" % _REF_MAX
        from PIL import Image
        im = Image.open(path).convert("RGB")
    except Exception as e:
        return None, "could not read the reference: %s" % str(e)[:120]
    w, h = size
    out = Image.new("RGB", (w, h), (18, 21, 28))
    thumb = im.copy()
    thumb.thumbnail((w, h), Image.LANCZOS)
    out.paste(thumb, ((w - thumb.width) // 2, (h - thumb.height) // 2))
    _rule(out)
    return out, ""


def _rule(im) -> None:
    """Rule the reference in the same 0..1 the part positions are reported in.

    Without it the two halves of the loop speak different languages: the model's parts come back
    as exact numbers, and the target can only be described in words like "a bit left of centre".
    With it, both are coordinates and the correction is a subtraction. x runs 0 at the left edge
    to 1 at the right; y runs 0 at the TOP to 1 at the bottom, which is the picture's own order
    and the one `F.where` uses.

    Drawn faint on purpose. A grid that competes with the artwork has replaced one reading problem
    with another.
    """
    try:
        from PIL import ImageDraw
    except Exception:                                # pragma: no cover - Pillow is a hard dep
        return
    w, h = im.size
    d = ImageDraw.Draw(im, "RGBA")
    for i in range(1, 4):
        x, y = round(w * i / 4.0), round(h * i / 4.0)
        d.line([(x, 0), (x, h)], fill=(255, 255, 255, 34), width=1)
        d.line([(0, y), (w, y)], fill=(255, 255, 255, 34), width=1)
    # Only the quarter marks are labelled. Ticks every tenth would be more precise and less
    # readable, and the agent can interpolate between two labels perfectly well.
    for i in range(1, 4):
        t = "%.2f" % (i / 4.0)
        d.text((round(w * i / 4.0) + 3, 3), t, fill=(150, 150, 150))
        d.text((3, round(h * i / 4.0) + 2), t, fill=(150, 150, 150))
    # A flat grey, not white-with-alpha: PIL draws text fill opaque whatever the alpha
    # says, and a label at full white is a label that competes with the artwork.
    d.text((3, 3), "0,0", fill=(150, 150, 150))


# How small a part has to be before it is honestly unchecked. Below this a feature is a smudge:
# the claws that came out pointing the wrong way measured about ten pixels on the sheet that
# passed them, twelve times.
_TOO_SMALL_PX = 24


def _findings(stats: dict, has_ref: bool, focused: list, ref_off: bool = False) -> list:
    """The short lines an agent reads before it opens the picture."""
    out: list = []
    parts = [p for p in (stats.get("parts") or []) if isinstance(p, dict)]
    # `px` is the size of ONE of them. The union of all twelve claws is 101px and tells you
    # nothing; a single claw is 12px, and that is the number that decides whether it was seen.
    small = [p for p in parts
             if isinstance(p.get("px"), (int, float)) and p.get("tris")
             and p["px"] < _TOO_SMALL_PX
             and str(p.get("name", "")).lower() not in [f.lower() for f in focused]]
    if small:
        small.sort(key=lambda p: p.get("px") or 0)
        names = ", ".join("%s (%dpx x%d)" % (p.get("name") or "?", p.get("px") or 0,
                                             p.get("meshes") or 1)
                          for p in small[:5])
        more = "" if len(small) <= 5 else " and %d more" % (len(small) - 5)
        out.append("NOT CHECKED: %d part%s render under %dpx at this framing, so nothing in the "
                   "sheet says whether they are right - %s%s. Re-run with "
                   "--focus %s to actually see %s."
                   % (len(small), "s" if len(small) > 1 else "", _TOO_SMALL_PX, names, more,
                      ",".join(str(p.get("name") or "") for p in small[:3]),
                      "them" if len(small) > 1 else "it"))
    # SHADOWS, AS A NUMBER, AND THE CONTROL THAT PRODUCED IT.
    #
    # The switch reached the light, the mesh instances and the shadow camera and still changed
    # nothing anybody could see, for two separate reasons. `F.studio` runs before the agent's
    # code, so the traverse that flags every mesh walked an empty scene. And the obvious way to
    # check - flip `castShadows`, difference two frames - reads zero even when the shadow is
    # plainly on screen, because PlayCanvas does not recompile a material when a light's
    # `castShadows` changes. `shadowIntensity` is a uniform and needs no recompile.
    #
    # Measured, one box over one plate, moving only that uniform: studio 0.0% of the frame at
    # peak 0, flat 0.6% at 34, reference 0.6% at 75, hard 0.6% at 164. The same shadow over the
    # same area, four amounts of visible - so `peak`, not `share`, says whether a person sees it.
    sh = stats.get("shadow")
    if isinstance(sh, dict):
        share = float(sh.get("share") or 0.0)
        peak = int(sh.get("peak") or 0)
        if peak < 8:
            out.append("SHADOWS: on, and the picture is the same without them - %0.1f%% of the "
                       "frame moved, by at most %d levels. Either nothing is standing over "
                       "anything (a figure alone only shadows itself; pass ground=true for a "
                       "floor), or the surface it falls on has no headroom left. The default "
                       "studio rig measures exactly 0 on a white plate; light=\"hard\" measured "
                       "peak 164 on the same subject." % (share, peak))
        elif peak < 45:
            out.append("SHADOWS: there, and too faint to read - %0.1f%% of the frame, peak %d "
                       "levels. The ambient floor and the fill are carrying the surface. "
                       "Measured on one subject: flat 34, reference 75, hard 164."
                       % (share, peak))
        elif share > 8.0:
            out.append("SHADOWS: %0.1f%% of the frame at peak %d. Over a twelfth of the picture "
                       "is shadow difference, which is usually acne across a wide flat surface "
                       "rather than form - look before you trust it." % (share, peak))
        else:
            out.append("SHADOWS: %0.1f%% of the frame, peak %d levels." % (share, peak))
    sol = stats.get("solidity") or {}
    n = int(sol.get("surfaces") or 0)
    if n >= 8:
        pairs = int(sol.get("overlapping_pairs") or 0)
        line = ("BUILD: %d separate surfaces, %d pair%s of them interpenetrating; the largest "
                "holds %d%% of the triangles."
                % (n, pairs, "" if pairs == 1 else "s", int(sol.get("largest_share") or 0)))
        if pairs >= max(4, n // 4):
            # Said as a description, never as a rule. A kitbash is the right build for a rifle
            # and the wrong one for a creature that has to read as one soft skin, and no tool can
            # know which of those is being made. What it CAN do is say what was built, early
            # enough that changing method still costs less than patching every seam by hand.
            line += (" Where a subject has to read as one continuous skin, that is usually one "
                     "implicit surface (blobs joined by a smooth minimum) or a weld - "
                     "ops.skin(root) in the mesh tools. For hard-surface work this is fine.")
        out.append(line)
    st = stats.get("stance") or {}
    if st.get("count"):
        out.append("STANCE: %d point%s touch the ground - %d in front of the body centre, %d "
                   "behind it. The tool cannot read the reference's pose; compare this against "
                   "the first panel, and remember a raised limb is a pose decision the geometry "
                   "will not make for you."
                   % (st["count"], "s" if st["count"] > 1 else "", st.get("front", 0),
                      st.get("rear", 0)))
    # NOT WHEN IT WAS SWITCHED OFF ON PURPOSE. `ref:"none"` exists so a picture of something the
    # project's stored reference has nothing to say about - a hillside, in a project whose
    # reference is a character - costs no image tokens. Answering it with "pass ref=<image path>"
    # is the tool arguing with the caller about a decision the caller just made.
    if not has_ref and not ref_off:
        out.append("NO REFERENCE: pass ref=<image path> once and it is remembered for this "
                   "project, then every sheet carries the target beside the render.")
    return out


def _settles(fn):
    """A call that opens an activity row always closes it: on success, early return, or throw.

    `_done` was only reached on the paths that finished properly. A forge that could not serve its
    project returned early, and the Engine window said "building · probe2 · 3q" for 27 minutes.
    The row is the one THIS thread opened, so a newer call's row is never closed by an older one.
    """
    import functools

    @functools.wraps(fn)
    def run(project, *a, **kw):
        _ACT_TL.row = None
        res = None
        try:
            res = fn(project, *a, **kw)
            return res
        except Exception as ex:
            res = {"ok": False, "error": str(ex)[:200]}
            raise
        finally:
            row = getattr(_ACT_TL, "row", None)
            if row is not None and not row.get("ended"):
                good = isinstance(res, dict) and res.get("ok") is not False
                row.update({"phase": "done" if good else "failed",
                            "detail": "" if good else (str((res or {}).get("error") or "")[:200]
                                                       or "the call ended without an answer"),
                            "ended": time.time()})
            _ACT_TL.row = None
    return run


# A vertex tint this wide is painted on BEFORE any lamp, so no rig setting undoes it. The
# re-run's hair carried 0.50 to 1.42 - a 2.8x range - and 47 separate strands rendered as one
# glossy mass with no shadow between any two of them.
TINT_WIDE = 2.0
# ...and it is LIGHT painted in only where it brightens the material past its own colour. Baked
# occlusion and painted albedo are multipliers at or under 1 and are exactly what a brief asks for:
# the goblin A/B's forge build was told on every one of its parts that it carried "a vertex tint
# 42.9 times lighter at its brightest than at its darkest" - one crease vertex against one white
# one, min against max. The page now sends the 5th and 95th percentile instead (forge 21).
TINT_LIGHT = 1.05


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _health_finding(rows) -> list:
    """What a modelling package says about a mesh and the bench used to say nothing about."""
    out: list = []
    for r in (rows or [])[:12]:
        if not isinstance(r, dict):
            continue
        name = str(r.get("part") or "?")
        tris = int(r.get("tris") or 0)
        if r.get("normals") is False and tris > 8:
            out.append("MESH: `%s` has no normals, so it is flat-shaded and every facet of it "
                       "shows. Compute them, or the smooth form you modelled cannot be seen."
                       % name)
        bad = int(r.get("degenerate") or 0)
        if bad and tris and bad >= max(4, tris * 0.005):
            out.append("MESH: `%s` has %d triangles with no area, of %d. They cost a draw and "
                       "show as black slivers where two of them touch." % (name, bad, tris))
        tint = _num(r.get("tint")) or 0.0
        lo, hi = _num(r.get("tint_p5")), _num(r.get("tint_p95"))
        # UNLIT parts are skipped: on MeshBasicMaterial the vertex colour IS the picture, and the
        # line fired six to eight times on every call of a deliberately painted tree.
        if r.get("unlit"):
            continue
        if lo is not None and hi is not None:
            if hi > TINT_LIGHT and tint >= TINT_WIDE:
                out.append("MESH: `%s` carries a vertex tint from %.2f to %.2f of its material's "
                           "colour (5th to 95th percentile), %.1f times lighter at its brightest "
                           "than at its darkest. Above 1.0 that is light painted on before any "
                           "lamp, so no rig setting undoes it - and a mass whose own parts are "
                           "already lit cannot show the shadow between them. Occlusion or albedo "
                           "baked at 1.0 and under is never counted here." % (name, lo, hi, tint))
        elif tint >= TINT_WIDE:
            # A row from a page older than forge 21: min and max are all it carries.
            out.append("MESH: `%s` carries a vertex tint %.1f times lighter at its brightest than "
                       "at its darkest. That is painted on before any lamp, so no rig setting "
                       "undoes it - and a mass whose own parts are already lit cannot show the "
                       "shadow between them." % (name, tint))
    return out


# ---------------------------------------------------------------------------
# Holes a person can see, and contrast against the reference
# ---------------------------------------------------------------------------
def _checks_on() -> bool:
    """HOLE, CONTRAST and SYMMETRY - the checks the goblin A/B added - behind ONE switch, read the
    way forge_detail and forge_colours are: Settings → Studio engine, `forge_checks`. On unless
    it is switched off, so a check that misbehaves costs the user one click, not a release."""
    return _engine_pref("forge_checks") is not False


def _hole_finding(got) -> list:
    """HOLE lines for the open edge loops nothing covers. See F.holes in live_forge.py.

    `ops.check` counts every open edge as a hole, so a surface that deliberately ends inside
    another part (267 open edges inside the forge goblin's helmet) read the same as the real gap
    three blind graders saw at the same goblin's sword wrist. Only a loop whose edge is not inside
    another part, whose middle nothing fills, and that is not the rim of an open shell is named."""
    rows = got.get("rows") if isinstance(got, dict) else got
    out: list = []
    seen = 0
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        for h in (r.get("holes") or []):
            seen += 1
            if len(out) >= 4:
                continue
            across = _num(h.get("across")) or 0.0
            size = ("%.1f cm" % (across * 100.0)) if across < 1.0 else ("%.2f m" % across)
            at = h.get("at") if isinstance(h.get("at"), list) and len(h.get("at")) == 3 else None
            out.append("HOLE: `%s` has an open edge loop %s across that can be seen from outside%s "
                       "- no other part covers its edge or fills its middle. Close it, or let the "
                       "surface end inside its neighbour."
                       % (r.get("part") or "?", size,
                          (" (at %.2f, %.2f, %.2f)" % tuple(float(x) for x in at)) if at else ""))
    more = seen - len(out)
    if more > 0:
        out.append("HOLE: %d more open loop%s like these - `holes` in the answer has them all."
                   % (more, "" if more == 1 else "s"))
    return out


def _hole_summary(res: dict, got) -> None:
    """The open-edge census as numbers beside the findings, only when anything is open at all:
    how many loops end inside another part, are filled by one, continue as another part (one
    surface cut in two), are narrower than a pixel, rim an open shell, or show."""
    rows = got.get("rows") if isinstance(got, dict) else None
    if not rows:
        return
    tot = {"open_edges": 0, "visible": 0, "hidden": 0, "plugged": 0, "seam": 0, "pinhole": 0,
           "ground": 0, "shell": 0}
    for r in rows:
        if isinstance(r, dict):
            for k in tot:
                tot[k] += int(r.get(k) or 0)
    tot["parts"] = [r for r in rows if isinstance(r, dict) and r.get("visible")][:8]
    if got.get("partial"):
        tot["partial"] = True          # the time budget ran out; later loops were not judged
    res["holes"] = tot


# ---------------------------------------------------------------------------
# Symmetry: what a front view would show, without taking one (see F.symmetry in live_forge.py)
# ---------------------------------------------------------------------------
# Names that belong on the midline of a figure. A match is "part of the name": helmetDome is one.
_CENTRED = ("belt", "buckle", "nose", "chin", "mouth", "beard", "breastplate", "chest", "spine",
            "helmet", "head", "neck", "pelvis", "tail")
# Things a figure HOLDS or carries: off the midline by nature. The goblin's sword crosses its body,
# and its blade read "9.3 cm right of the middle". A part with such a name, or inside a group with
# one (blade, grip and pommel under `sword`), is left out of the midline check.
_PROPS = ("sword", "blade", "shield", "weapon", "prop", "staff", "spear", "axe", "bow", "arrow",
          "gun", "rifle", "pistol", "club", "hammer", "mace", "dagger", "knife", "wand", "torch",
          "lantern", "flag", "banner", "scabbard", "sheath", "hilt", "crossguard", "pommel")
# Off the midline by more than this share of the figure's height is worth a line: 1.5 cm on a 1 m
# goblin, whose buckle sat 4.6 cm off, its tassets 5.8 cm. Further off than SYM_FAR it is not a
# midline part at all.
SYM_OFF = 0.015
SYM_FAR = 0.15
# Twins whose sizes differ by more than this (linear, from surface area - which does not change
# when an arm is posed, unlike its box) are named; a pair this far apart in height gets one note.
SYM_SIZE = 0.15
SYM_HEIGHT = 0.10


def _name_hit(name: str, words) -> bool:
    """Does this part name carry one of these words? A short word must be a whole token of the
    name - camelCase or separated - so `bow` is not found in elbowL, nor `chin` in machine; a
    longer one may sit inside a longer word (`sword` in greatsword)."""
    s = str(name or "")
    tokens = {t.lower() for t in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", s)}
    low = s.lower()
    return any((w in tokens) if len(w) <= 4 else (w in low) for w in words)


def _sym_twins(parts: list) -> list:
    """[(left, right)] part rows that are one another's mirror by name: L/R, Left/Right, _l/_r, or
    exactly two numbered copies."""
    from .live_detail import side_key
    sided: dict = {}
    for p in parts:
        k = side_key(p.get("name") or "")
        if k:
            sided.setdefault(k[0], {}).setdefault(k[1], p)
    pairs = []
    for sides in sided.values():
        if "L" in sides and "R" in sides:
            pairs.append((sides["L"], sides["R"]))
        elif len(sides) == 2 and all(s.startswith("#") for s in sides):
            a, b = [sides[s] for s in sorted(sides, key=lambda x: int(x[1:]))]
            pairs.append((a, b))
    return pairs


def _cm(v: float) -> str:
    v = abs(float(v))
    return ("%.1f cm" % (v * 100.0)) if v < 1.0 else ("%.2f m" % v)


def _symmetry_finding(sym) -> list:
    """SYMMETRY lines from every part's world box and surface area: centred parts off the midline,
    twins of different sizes, and at most one note on twins set unevenly in height.

    The midline is x = 0 - the builder's own origin - whenever the asset straddles it, else the
    middle of its box. Under glTF +x is the subject's own LEFT. Only a figure with at least one
    left/right pair is judged for the midline: a prop or a scene has no midline to keep."""
    if not isinstance(sym, dict):
        return []
    parts = [p for p in (sym.get("parts") or []) if isinstance(p, dict)
             and isinstance(p.get("lo"), list) and isinstance(p.get("hi"), list)
             and len(p["lo"]) == 3 and len(p["hi"]) == 3]
    box = sym.get("box") or {}
    try:
        lo, hi = [float(v) for v in box["lo"]], [float(v) for v in box["hi"]]
    except (KeyError, TypeError, ValueError):
        return []
    height = hi[1] - lo[1]
    if not parts or height <= 1e-9:
        return []
    mid = 0.0 if lo[0] < 0.0 < hi[0] else (lo[0] + hi[0]) / 2.0
    pairs = _sym_twins(parts)
    out: list = []
    if pairs:
        twins = {p["name"] for pr in pairs for p in pr}
        off = []
        for p in parts:
            name = str(p.get("name") or "")
            if name in twins:
                continue
            held = [str(u) for u in (p.get("under") or [])] + [name]
            if any(_name_hit(h, _PROPS) for h in held):
                continue
            lo_x, hi_x = float(p["lo"][0]), float(p["hi"][0])
            cx = (lo_x + hi_x) / 2.0
            centred = _name_hit(name, _CENTRED)
            # A MIDLINE PART SLID OVER, not a side object that grazes the midline: 15% of its width
            # or more on each side of it (the goblin's tassets: 24%), or a name that belongs there
            # (the buckle, which slid clean off it) - and never further off than SYM_FAR of the
            # figure. The cartoon tree's side bushes cross the midline by 4-6% of their width.
            width = max(1e-9, hi_x - lo_x)
            straddles = lo_x < mid < hi_x and min(mid - lo_x, hi_x - mid) >= 0.15 * width
            if (centred or straddles) and SYM_OFF * height < abs(cx - mid) <= SYM_FAR * height:
                off.append((abs(cx - mid), name, cx, centred))
        off.sort(reverse=True)
        for _d, name, cx, centred in off[:6]:
            out.append("SYMMETRY: `%s`%s sits %s %s of the middle (x = %+.3f) - from the front it "
                       "reads off-centre. A part on the midline stays on x = %g; if it misses its "
                       "pixel in a three-quarter reference, the camera is wrong, not the part."
                       % (name, ", a centred part," if centred else "", _cm(cx - mid),
                          "left" if cx > mid else "right", cx, round(mid, 4)))
    uneven = None
    for a, b in pairs:
        sa, sb = float(a.get("area") or 0.0), float(b.get("area") or 0.0)
        if sa > 0 and sb > 0:
            ra = math.sqrt(sa / sb)
        else:                       # no area: the box diagonals, which a pose can change
            da = math.dist(a["lo"], a["hi"])
            db = math.dist(b["lo"], b["hi"])
            ra = da / db if db > 0 else 1.0
        big, small = (a, b) if ra >= 1.0 else (b, a)
        pct = (max(ra, 1.0 / ra) - 1.0) * 100.0 if ra > 0 else 0.0
        if pct > SYM_SIZE * 100.0:
            out.append("SYMMETRY: `%s` is %d%% larger than `%s` - twins that should mirror each "
                       "other. Measured on their surfaces, so a posed limb is not counted as a "
                       "size." % (big["name"], round(pct), small["name"]))
        dy = ((float(a["lo"][1]) + float(a["hi"][1])) - (float(b["lo"][1]) + float(b["hi"][1]))) / 2.0
        if abs(dy) > SYM_HEIGHT * height and (uneven is None or abs(dy) > abs(uneven[0])):
            uneven = (dy, a, b)
    if uneven:
        dy, a, b = uneven
        hi_p, lo_p = (a, b) if dy > 0 else (b, a)
        out.append("SYMMETRY: `%s` sits %s higher than `%s` - fine for a pose; worth a look if the "
                   "reference holds them level." % (hi_p["name"], _cm(dy), lo_p["name"]))
    return out


def _gap_finding(got) -> list:
    """GAP lines: pieces of the asset whose surface comes within 1% of the figure's height of
    nothing in the largest piece (F.gaps). A held prop joins the body through the hand on its grip;
    one the hand does not touch is named as that."""
    rows = got.get("rows") if isinstance(got, dict) else None
    out: list = []
    for r in (rows or []):
        if not isinstance(r, dict) or r.get("gap") is None:
            continue
        if len(out) >= 4:
            break
        parts = [str(p) for p in (r.get("parts") or []) if str(p)]
        if not parts:
            continue
        group = str(r.get("group") or "")
        if len(parts) == 1:
            label = "`%s`" % parts[0]
        else:
            label = "`%s` (%s)" % (group or parts[0], ", ".join(parts[:6]))
        prop = any(_name_hit(h, _PROPS) for h in parts + ([group] if group else []))
        out.append("GAP: %s floats %s off every other part - %s"
                   % (label, _cm(float(r["gap"])),
                      "a held prop the hand does not touch" if prop
                      else "from behind it reads as a gap"))
    return out


# Luminance levels the render's darks may sit above the reference's before it is worth a line, and
# the share of the reference's spread it may lose. Three of four blind graders called the forge
# goblin "paler and flatter" with "little ambient occlusion"; nothing on the bench said so.
CONTRAST_DARK = 25.0
CONTRAST_SPREAD = 0.70


def _tone_spread(im, mask):
    """[p5, p50, p95] of Rec.709 luminance over the subject's own pixels, or None."""
    import numpy as np
    a = np.asarray(im.convert("RGB"), dtype=np.float32)
    m = np.asarray(mask, bool)
    if m.shape != a.shape[:2]:
        return None
    try:
        # Two pixels in from the outline: an edge pixel is half backdrop, and a dark backdrop
        # would lend the render the very darks this is asking about.
        from scipy import ndimage
        inner = ndimage.binary_erosion(m, iterations=2)
        if int(inner.sum()) >= 200:
            m = inner
    except Exception:
        pass
    lum = (0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2])[m]
    if lum.size < 200:
        return None
    return [round(float(np.percentile(lum, q)), 1) for q in (5, 50, 95)]


def _contrast(render_im, ref_path: str, ref_mask: str = "") -> dict:
    """The luminance spread of the lit render beside the reference's, subject pixels only."""
    if render_im is None or not ref_path:
        return {}
    try:
        from PIL import Image
        tr = _tone_spread(render_im, _mask_of_photo(render_im))
        ref = Image.open(ref_path).convert("RGB")
        tf = _tone_spread(ref, _mask_of_reference(ref_path, ref_mask))
    except Exception:
        return {}
    if not tr or not tf:
        return {}
    return {"render": tr, "reference": tf}


def _contrast_finding(c: dict) -> str:
    """CONTRAST, only when the gap is large: darks 25 levels lighter, or under 70% of the spread."""
    r, f = (c or {}).get("render"), (c or {}).get("reference")
    if not r or not f:
        return ""
    dark = r[0] - f[0]
    sr, sf = r[2] - r[0], f[2] - f[0]
    narrow = sf > 1.0 and sr < CONTRAST_SPREAD * sf
    if dark <= CONTRAST_DARK and not narrow:
        return ""
    if dark > CONTRAST_DARK:
        why = "the darks are missing (cavities, creases, under the brim)"
    elif f[2] - r[2] > CONTRAST_DARK:
        why = "the lights are missing (the tops and edges the key light catches)"
    else:
        why = "the whole range is squeezed toward the middle"
    return ("CONTRAST: the render spans %d..%d where the reference spans %d..%d - %s. Luminance "
            "over the subject's own pixels, 5th to 95th percentile, at the same view. Occlusion "
            "baked into the colour and deeper creases bring the darks back; \"light\":"
            "\"reference\" deepens the shadows but cannot add a cavity the mesh does not have."
            % (round(r[0]), round(r[2]), round(f[0]), round(f[2]), why))


def _detail_mode(detail) -> tuple:
    """(named parts, mode). mode "" is off, "auto" is numbers first, "all" pictures every angle."""
    if detail is None:
        return [], ("" if _engine_pref("forge_detail") is False else "auto")
    if detail is False:
        return [], ""
    if isinstance(detail, (list, tuple)):
        names = [str(x).strip() for x in detail if str(x).strip()]
        return names, "all"
    s = str(detail).strip()
    if s.lower() in ("off", "false", "0", "no", "none", ""):
        return [], ""
    if s.lower() in ("all", "true", "on", "yes", "1", "auto") or detail is True:
        return [], ("auto" if s.lower() == "auto" else "all")
    return [p.strip() for p in s.split(",") if p.strip()], "all"


async def _forge_script(live) -> str:
    """Put the CURRENT forge script in the page. Returns "installed", "replaced" or "current".

    A tab survives a backend restart, and the script's own `if (window.__forge) return` kept the
    old one there — so every function added since was missing from the page that needed it. A
    stale script is disposed of and replaced. Only called on the paths that build, because the
    scene lives inside the script and a build replaces the scene anyway.
    """
    from .live_forge import FORGE, FORGE_VERSION
    have = await live.raw("window.__forge ? (window.__forge.version || 0) : -1")
    try:
        have = int(have)
    except (TypeError, ValueError):
        have = 0
    if have == FORGE_VERSION:
        return "current"
    if have != -1:
        await live.raw("try { window.__forge.dispose && window.__forge.dispose(); } catch (e) {}"
                       " try { delete window.__forge; } catch (e) { window.__forge = undefined; }")
    await live.raw(FORGE, wait=False)
    return "installed" if have == -1 else "replaced"


# ---------------------------------------------------------------------------
# The forge heals a tab whose imports cannot resolve
#
# A forge tab opened before `index.html` had its import map keeps THAT document. Every
# `import('./src/assets.js')` whose file says `from 'three'` then fails with `Failed to resolve
# module specifier "three"`, for as long as the tab lives - which is hours - and no answer said a
# reload would fix it. Measured on a throwaway page opened before its map was written: the forge
# failed on exactly that line, and the same code built once the tab was reloaded.
#
# So a build that fails to import is healed, one step at a time and never more than once a call:
#   1. RELOAD, when a reload can help: the served html maps the name and the tab's document does
#      not (a stale tab), or the failure is a module the page remembers failing (Chrome keeps a
#      failed fetch in the document's module map; the file answers 200 now). A reload that cannot
#      help is skipped - it would only restart the game under the agent.
#   2. AN IMPORT MAP for a bare `three` or `playcanvas` that still fails, pointed at the engine the
#      forge already built with, so the agent's module and the forge share one copy. Chrome 131,
#      the one here, refuses a map added once any module has loaded (measured), so if the late
#      map does not take, it goes in at document start on one more reload - where it does.
#   3. Otherwise the answer names the specifier and the one line that fixes the game itself.
# Every step it takes is reported in `heal`, because a healed tab is not a fixed game: the page
# the player opens still has no import map until someone writes one.
# ---------------------------------------------------------------------------
_IMPORT_FAIL = re.compile(r"Failed to resolve module specifier|Importing a module script failed|"
                          r"error loading dynamically imported module|"
                          r"Failed to fetch dynamically imported module", re.I)
_BARE_SPEC = re.compile(r"Failed to resolve module specifier\s*[\"'“‘]([^\"'”’]+)"
                        r"[\"'”’]")
_FETCH_URL = re.compile(r"Failed to fetch dynamically imported module:\s*(\S+)")
_ENGINE_FAMILIES = ("three", "playcanvas")


def import_failure(err) -> bool:
    """Did this build fail because a module would not import?"""
    return bool(err) and bool(_IMPORT_FAIL.search(str(err)))


def bare_specifier(err) -> str:
    """The bare name that would not resolve (`three`, `three/addons/x.js`), or ""."""
    m = _BARE_SPEC.search(str(err or ""))
    return m.group(1).strip() if m else ""


def _family(spec: str) -> str:
    if not spec:
        return ""
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") else parts[0]


def _import_map_for(spec: str, href: str) -> dict:
    """The map that sends `spec` to the engine the forge already loaded, subpaths included."""
    fam = _family(spec)
    imports = {fam: href}
    if fam == "three":
        m = re.match(r"^(.*/)build/[^/]+$", href.split("?")[0])
        if m:
            imports["three/"] = m.group(1)
            imports["three/addons/"] = m.group(1) + "examples/jsm/"
    return {"imports": imports}


def _module_entry(pkg_dir: Path) -> str:
    """A package's ES module entry, relative to its folder, from its own package.json."""
    try:
        meta = json.loads((pkg_dir / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    exp = meta.get("exports")
    dot = exp.get(".") if isinstance(exp, dict) else exp
    for cand in ((dot.get("import") if isinstance(dot, dict) else None),
                 (dot.get("default") if isinstance(dot, dict) else None),
                 dot if isinstance(dot, str) else None, meta.get("module"),
                 meta.get("browser") if isinstance(meta.get("browser"), str) else None,
                 meta.get("main")):
        if isinstance(cand, dict):
            cand = cand.get("default")
        if isinstance(cand, str) and cand.strip():
            return cand.strip().lstrip("./")
    return ""


def _import_fix(project: str, err: str) -> str:
    """The one line that fixes the GAME, not just the Studio's tab."""
    spec = bare_specifier(err)
    root = Path(project) if project else None
    if spec:
        fam = _family(spec)
        pkg = (root / "node_modules" / fam) if root else None
        entry = _module_entry(pkg) if pkg is not None and pkg.is_dir() else ""
        dev = ""
        try:
            from . import dev_server
            dev = dev_server.script_for(root) or "" if root else ""
        except Exception:
            dev = ""
        if entry:
            line = {"imports": {fam: "./node_modules/%s/%s" % (fam, entry)}}
            if fam == "three":
                line["imports"]["three/addons/"] = "./node_modules/three/examples/jsm/"
            fix = ('add to index.html, before the first module script: <script type="importmap">%s'
                   "</script>" % json.dumps(line, separators=(",", ":")))
            if dev:
                fix += " - or open the game through its dev server (npm run %s), which resolves " \
                       "bare imports itself" % dev
            return fix
        if dev:
            return ("open the game through its dev server (npm run %s): %r is a bare import that "
                    "only a bundler resolves, and a plain file server does not" % (dev, spec))
        return ("%r is not installed here: npm install %s, then map it in index.html with "
                '<script type="importmap">{"imports":{"%s":"./node_modules/%s/<its .js entry>"}}</script>'
                % (spec, fam, fam, fam))
    m = _FETCH_URL.search(str(err or ""))
    if m:
        return ("%s did not load. Check the path is right and that the server answers it as "
                "JavaScript; a missing file comes back as the site's index.html" % m.group(1))
    return "a module did not import; the first line of the error names it"


def _status_of(url: str) -> int:
    """The HTTP status a URL answers now, or 0. Bounded, because it runs inside a call.

    Loopback only. The URL comes out of a page's error text, and the backend does not fetch
    whatever host a page happens to name; a game's own dev server and this Studio are both local."""
    import urllib.request
    import urllib.error
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return 0
    if urlparse(url).scheme not in ("http", "https") or host not in ("127.0.0.1", "localhost", "::1"):
        return 0
    try:
        req = urllib.request.Request(url, method="GET", headers={"cache-control": "no-cache"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return int(r.status)
    except urllib.error.HTTPError as ex:
        return int(ex.code)
    except Exception:
        return 0


async def _sidecar_again(live, e: dict) -> None:
    """Hand the reloaded page its saved edits again, as `open` does for a page it loads."""
    try:
        sidecar = Path(str(e.get("project") or "")) / "studio.edits.json"
        if not sidecar.is_file() or not _engine_pref("live_sidecar", True):
            return
        url = _sidecar_url(sidecar.parent)
        await live.raw("window.__live && __live.sidecar && __live.sidecar(%s)" % json.dumps(url),
                       wait=False)
    except Exception:
        pass


async def _reload_tab(live, e: dict, timeout: float = 20.0, sidecar: bool = True) -> float:
    """Reload the tab in place and wait for the new document. Seconds taken.

    The shim is registered for the new document first, so it sees that document's first error
    the way `open` arranges; the old document carries a mark, so `complete` is never read from
    the page that is being thrown away. `sidecar=False` for `open`, which hands the saved edits
    over itself."""
    t0 = time.monotonic()
    try:
        await live.call("Page.addScriptToEvaluateOnNewDocument", {"source": SHIM})
    except Exception:
        pass
    try:
        await live.raw("window.__studioOldDoc = 1", wait=False)
    except Exception:
        pass
    await live.call("Page.reload", {"ignoreCache": True})
    state = ""
    while time.monotonic() - t0 < timeout:
        await asyncio.sleep(0.15)
        try:
            state = str(await live.raw("window.__studioOldDoc ? 'old' : document.readyState") or "")
        except Exception:
            continue                      # the context is being swapped; ask again
        if state == "complete":
            break
    if state != "complete":
        raise RuntimeError("the tab did not finish reloading in %ds (last state %r)"
                           % (int(timeout), state))
    # Module scripts have run by `complete`; what they start asynchronously has not, and the
    # forge's engine search reads the page's resource list.
    await asyncio.sleep(0.4)
    await _landed(live, str(e.get("url") or ""))
    e["bridged"] = False                  # a new document has a new heap; the old pin is gone
    if not await _shim_present(live):
        await _install_shim(live, for_future_loads=False)
    if sidecar:
        await _sidecar_again(live, e)
    return time.monotonic() - t0


def _forge_run_js(code: str) -> str:
    """Code run the way the forge runs an agent's, answering JSON `{e}` - for a bench put back."""
    return ("(async()=>{const __c=__forge.ctx();const {pc,THREE,engine,app,device,renderer,scene,"
            "camera,root,forge,add,clear,log,params}=__c;let __e;try{await (async()=>{%s})();}"
            "catch(err){__e=String(err&&err.stack||err);}return JSON.stringify({e:__e});})()"
            "\n//# sourceURL=studio-forge.js" % code)


async def _restart_forge(live, opts: str) -> dict:
    await _forge_script(live)
    return await live.ask("__forge.ensure(%s)" % opts) or {}


_TRIED = re.compile(r"tried \d+ URLs \((.*?)\)\s*(?:\[at |$)", re.S)


async def _real_cause(live, err: str) -> tuple[str, str]:
    """(the error that explains it, what kind) for an import that tried several URLs.

    `openAsset` - the thumbnails, the Library - tries a module at several URLs and reports only
    the FIRST failure, which on a static server is the Vite-only `/@fs/` path answering 404. On the
    throwaway heal page that hid the real cause completely: the one URL that did answer (the
    Studio's own `/api/engine/source`) loaded and then failed on its bare `'three'`. So each URL it
    tried is asked: the first that answers 2xx is imported under a fresh query, and whatever that
    says is the cause. `kind` is "bare", "remembered" (it imports fine now), "missing" (none
    answered) or "" when nothing more could be learned."""
    m = _TRIED.search(str(err or ""))
    if not m:
        return err, ""
    urls = [u.strip().rstrip("…").strip() for u in m.group(1).split(", ")]
    urls = [u for u in urls if u.startswith("http")][:4]
    answered = False
    for u in urls:
        code = await asyncio.to_thread(_status_of, u)
        if not 200 <= code < 300:
            continue
        answered = True
        probe = u + ("&" if "?" in u else "?") + "studioprobe=%d" % int(time.time() * 1000)
        try:
            why = str(await live.raw("import(%s).then(() => '', (e) => String(e && e.message || e))"
                                     % json.dumps(probe)) or "")
        except Exception as ex:                              # noqa: BLE001
            why = str(ex)
        if not why:
            return ("%s imports under a fresh URL - the page remembered an earlier failure" % u,
                    "remembered")
        if bare_specifier(why):
            return "%s (importing %s)" % (why, u), "bare"
        return "%s (importing %s)" % (why, u), ""
    if urls and not answered:
        return err, "missing"
    return err, ""


async def _heal_imports(live, e: dict, err: str, opts: str, state: dict) -> bool:
    """One step of the heal. True when the build is worth running again.

    `state` belongs to one call and records how far the heal has got, so it can never loop: at
    most one reload for staleness, one import map, then the answer."""
    did = state.setdefault("did", [])
    step = int(state.get("step") or 0)
    kind = ""
    if not bare_specifier(err) and _TRIED.search(str(err)):
        real, kind = await _real_cause(live, str(err))
        if real != err:
            did.append("asked each URL it tried: %s" % real[:220])
            err = real
            state["cause"] = real[:400]
    spec = bare_specifier(err)
    fam = _family(spec)
    state["specifier"] = spec or state.get("specifier", "")
    if step == 0:
        state["step"] = 1
        # WHICH NAME TO CHECK THE TAB AGAINST. The failing specifier when the error names one;
        # otherwise the engine the forge built with, because a three.js game's builders import
        # 'three' and a stale tab fails them all - whatever the error text happened to lead with.
        engine = ""
        if not spec:
            try:
                engine = str(await live.raw("window.__forge && __forge.ctx ? __forge.ctx().engine : ''")
                             or "")
            except Exception:
                engine = ""
        name = spec or engine
        ms: dict = {}
        if name:
            try:
                ms = await live.ask("__forge.mapState(%s)" % json.dumps(name), depth=3) or {}
            except Exception:
                ms = {}
        reload_why = ""
        if ms.get("stale"):
            reload_why = "the served index.html maps %r and this tab's document does not" % _family(name)
        elif spec:
            if ms.get("served") is None:
                reload_why = "the served index.html could not be read, so a reload was tried"
            else:
                did.append("no reload: the served index.html does not map %r either" % fam)
        elif kind == "remembered":
            reload_why = "a module the page had failed to load imports now"
        elif kind == "missing":
            did.append("no reload: none of the URLs it tried answered")
            state["step"] = 3
            return False
        else:
            m = _FETCH_URL.search(str(err))
            if m and not _TRIED.search(str(err)):
                code = await asyncio.to_thread(_status_of, m.group(1))
                if 200 <= code < 300:
                    reload_why = ("%s answers %d now - the page remembered an earlier failure"
                                  % (m.group(1), code))
                else:
                    did.append("no reload: %s answers %s" % (m.group(1), code or "nothing"))
                    state["step"] = 3
                    return False
            else:
                reload_why = "a module did not import"
        if reload_why:
            try:
                secs = await _reload_tab(live, e)
            except Exception as ex:
                did.append("the reload failed: %s" % str(ex)[:200])
                state["step"] = 3
                return False
            state["reloads"] = int(state.get("reloads") or 0) + 1
            did.append("reloaded the tab in %.1fs: %s" % (secs, reload_why))
            built = await _restart_forge(live, opts)
            if not built.get("ok"):
                did.append("the forge did not come back: %s" % str(built.get("error") or "")[:200])
                state["step"] = 3
                return False
            return True
        step = 1                          # nothing a reload could do: straight to the map
    if step == 1:
        state["step"] = 2
        if fam not in _ENGINE_FAMILIES:
            return False
        href = str(await live.raw("window.__forge && __forge.engineHref ? __forge.engineHref() : ''")
                   or "")
        built_with = str(await live.raw("window.__forge && __forge.ctx ? __forge.ctx().engine : ''")
                         or "")
        if not href or built_with != fam:
            did.append("no import map: the forge's engine is %r, not %s" % (built_with or "unknown", fam))
            return False
        mp = _import_map_for(spec, href)
        late = await live.ask("__forge.lateMap(%s,%s)" % (json.dumps(mp), json.dumps(fam)),
                              depth=3) or {}
        if late.get("ok"):
            state["map"] = "late"
            did.append("gave the page an import map for %s, added late: %s" % (fam, href))
            return True
        # REFUSED, as Chrome before 133 refuses any map once a module has loaded. In at document
        # start instead, on one more reload.
        from .live_forge import IMPORT_MAP_AT_START
        try:
            await live.call("Page.addScriptToEvaluateOnNewDocument",
                            {"source": IMPORT_MAP_AT_START.replace("__MAP__", json.dumps(mp))})
            secs = await _reload_tab(live, e)
        except Exception as ex:
            did.append("the import map could not be put in at document start: %s" % str(ex)[:200])
            return False
        state["reloads"] = int(state.get("reloads") or 0) + 1
        took = str(await live.raw("String(window.__studioImportMap || '')") or "")
        state["map"] = "document-start"
        did.append("this Chrome refused a late import map (%s); put one in at document start "
                   "and reloaded in %.1fs: %s" % (str(late.get("error") or "")[:80], secs,
                                                   took or "?"))
        built = await _restart_forge(live, opts)
        return bool(built.get("ok"))
    return False


async def _healing(live, e: dict, opts: str, attempt, state: dict, after_reload=None):
    """Run `attempt()` -> (value, error); on an import failure heal a step and run it again.

    `after_reload` puts back what a reload threw away (the bench, for a forge that appends).
    Returns the last value; `state` says what was done, and carries the fix when nothing
    healed it."""
    state.setdefault("project", str(e.get("project") or ""))
    while True:
        value, err = await attempt()
        if not err or not import_failure(err):
            if state.get("did"):
                state["healed"] = True        # whatever else it did, it imported this time
            return value
        if int(state.get("step") or 0) >= 2:
            break
        reloads = int(state.get("reloads") or 0)
        if not await _heal_imports(live, e, str(err), opts, state):
            break
        if after_reload and int(state.get("reloads") or 0) > reloads:
            try:
                await after_reload()
            except Exception as ex:                          # noqa: BLE001
                state.setdefault("did", []).append("could not put the bench back: %s"
                                                   % str(ex)[:160])
    state["healed"] = False
    state["error"] = str(err)[:400]
    state["fix"] = _import_fix(str(e.get("project") or ""), str(err))
    return value


def _heal_report(state: dict) -> dict:
    """What an answer says about a heal, or {} when none was needed."""
    if not state.get("did") and not state.get("fix"):
        return {}
    out = {"healed": bool(state.get("healed")), "did": list(state.get("did") or [])}
    for k in ("specifier", "map", "reloads", "fix"):
        if state.get(k):
            out[k] = state[k]
    if out["healed"] and state.get("map"):
        # A healed TAB. The game a player opens still cannot import the name.
        out["fix"] = _import_fix(str(state.get("project") or ""),
                                 'Failed to resolve module specifier "%s"' % state.get("specifier", ""))
    return out


def _heal_finding(report: dict) -> list:
    """The line that goes FIRST in the findings when a build needed healing - or could not be."""
    if not report:
        return []
    steps = "; ".join(report.get("did") or []) or "nothing could be done"
    if report.get("healed"):
        line = "HEALED: the build could not import at first - %s. It imports now." % steps
        if report.get("map"):
            line += (" Only THIS tab has the import map; the game a player opens does not. "
                     "Fix the game: %s" % report.get("fix", ""))
        return [line]
    return ["IMPORT FAILED%s: %s. Fix: %s" % (" on %r" % report["specifier"] if report.get("specifier")
                                               else "", steps, report.get("fix") or "see the error")]


async def heal_build(live, e: dict, opts: str, run, state: Optional[dict] = None,
                     after_reload=None):
    """For any caller that builds in the forge tab (animate, terrain, debug): `run()` returns
    the build's error string ("" on success). Heals on an import failure and runs it again.
    Returns (error, heal report)."""
    st = state if state is not None else {}
    st.setdefault("project", str(e.get("project") or ""))

    async def attempt():
        err = await run()
        return err, err
    err = await _healing(live, e, opts, attempt, st, after_reload)
    return err, _heal_report(st)


async def _detail_pass(live, explicit: list, mode: str, tags, ref_path: str, view: str,
                       margin: float, views) -> Optional[dict]:
    """Close-ups of the parts that decide a character, held against the reference. See live_detail."""
    from . import live_detail as _ld
    try:
        present = await live.ask("__forge.present(%s)" % json.dumps(_ld.CANDIDATES), depth=4)
    except Exception:
        present = []
    targets = _ld.pick_targets(explicit, present if isinstance(present, list) else [], tags,
                               bool(ref_path))
    if not targets:
        return None
    # ONE HAND IS NOT BOTH HANDS: a target that reaches handL and handR is judged as the two.
    try:
        matches = await live.ask("__forge.matches(%s)" % json.dumps(targets), depth=4)
    except Exception:
        matches = {}
    targets = _ld.split_twins(targets, matches if isinstance(matches, dict) else {})
    try:
        return await _ld.run(live, targets, view, float(margin), ref_path, mode, views,
                             colours=_engine_pref("forge_colours") is not False)
    except Exception as ex:
        return {"rows": [], "error": str(ex)[:300]}


def _detail_out(project: str, det: Optional[dict], engine: str, label: str, res: dict) -> None:
    """Numbers, findings and the detail sheet from one detail pass, written into `res`."""
    if not det:
        return
    if det.get("error"):
        res.setdefault("notes", []).append("detail review: %s" % det["error"])
    rows = det.get("rows") or []
    if not rows:
        return
    from . import live_detail as _ld
    res["detail"] = _ld.summary(rows)
    lines = _ld.findings(rows, engine, str(det.get("view") or ""))
    problems = [x for x in lines if not x.startswith("DETAIL at ")]
    tail = [x for x in lines if x.startswith("DETAIL at ")]
    found = res.setdefault("findings", [])
    # An upside-down face outranks every other line on the list, so it goes first.
    for line in reversed(problems):
        found.insert(0, line)
    found.extend(tail)
    if det.get("rows") and not det.get("registered") and any(not r.get("missing") for r in rows):
        found.append("DETAIL: no reference to hold the close-ups against, so there are pictures "
                     "and no numbers. Pass ref=<image path> once.")
    shown = [r for r in rows if r.get("target") in (det.get("pictured") or [])]
    if not shown:
        return
    sheet = _ld.compose(shown, "%s · detail" % (label or "forge"),
                        "close-ups beside the same crop of the reference · compared from %s"
                        % det.get("view"))
    if sheet is None:
        return
    out = _LIVE_DIR / _slug(project) / ("detail-%d.png" % int(time.time() * 1000))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    res["detail_sheet"] = str(out)


# The directions the page's presets look FROM (live_forge.py, DIRS) - a copy, so two views can be
# compared here without a round trip. A spec string is read the way the page's dirOf reads it.
_VIEW_DIRS = {"3q": (1, 0.62, 1.15), "front": (0, 0, 1), "back": (0, 0, -1), "side": (1, 0, 0),
              "left": (-1, 0, 0), "top": (0, 1, 0.0001), "bottom": (0, -1, 0.0001),
              "low": (0.8, -0.32, 1), "hero": (0.55, 0.28, 1), "back3q": (-1, 0.5, -1)}
# How far a look may be from the angle its reference is scored from and still get a score.
NEAR_REF_DEG = 30.0


def _view_dir(v: str):
    """The unit direction a view string looks from, or None for one this cannot read."""
    import math
    s = str(v or "").strip()
    if "=" not in s:
        d = _VIEW_DIRS.get(s)
        if d is None:
            return None
    else:
        az = el = 0.0
        first = re.split(r"[,;\s]+", s)[0]
        if "=" not in first and first in _VIEW_DIRS:
            bx, by, bz = _VIEW_DIRS[first]
            nb = math.sqrt(bx * bx + by * by + bz * bz) or 1.0
            az = math.degrees(math.atan2(bx, bz))
            el = math.degrees(math.asin(max(-1.0, min(1.0, by / nb))))
        for kv in re.split(r"[,;\s]+", s):
            k, _, val = kv.partition("=")
            try:
                f = float(val)
            except ValueError:
                continue
            k = k.strip().lower()
            if k in ("az", "azimuth"):
                az = f
            elif k in ("el", "elev", "elevation"):
                el = f
        a, e = math.radians(az), math.radians(max(-89.0, min(89.0, el)))
        d = (math.sin(a) * math.cos(e), math.sin(e), math.cos(a) * math.cos(e))
    n = math.sqrt(sum(c * c for c in d)) or 1.0
    return tuple(c / n for c in d)


def _near_view(a: str, b: str, deg: float = NEAR_REF_DEG) -> bool:
    """Two views within `deg` degrees of each other; unreadable ones only when identical."""
    import math
    da, db = _view_dir(a), _view_dir(b)
    if da is None or db is None:
        return str(a) == str(b)
    dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(da, db))))
    return math.degrees(math.acos(dot)) <= deg


def _ref_view(project: str) -> str:
    """The angle this project's reference was last scored from, by a BUILD — not a look."""
    folder = _LIVE_DIR / _slug(project)
    try:
        files = sorted(folder.glob("forge-*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    except OSError:
        return ""
    for f in files[:40]:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rec.get("looked"):
            continue
        v = (rec.get("score") or {}).get("view")
        if v:
            return str(v)
    return ""


# THE STUDIO'S LOOK, AND IT IS STICKY. The rig was fixed for the life of the page, so an agent
# that needed near-black cloth had no way to ask for it: pure black rendered #2c2e30 and the only
# escape removed every reflection. A turnaround reference is orthographic and the bench was
# perspective, so the picture and the score compared two different projections. Both are now per
# call, and what a call does not mention is left as this session already had it — "set the rig
# once" is how an agent thinks, and a later `look` must not silently undo it.
_RIGS = ("studio", "flat", "reference", "hard")


def _studio_opts(ortho=None, light: str = "", env=None, ambient=None, exposure=None,
                 lights=None, shadows=None) -> dict:
    """Only what the caller actually asked for."""
    o: dict = {}
    if ortho is not None:
        o["ortho"] = bool(ortho)
    if shadows is not None:
        o["shadows"] = bool(shadows)
    if str(light or "").strip().lower() in _RIGS:
        o["light"] = str(light).strip().lower()
    for k, v in (("env", env), ("ambient", ambient), ("exposure", exposure)):
        if v is None or v == "":
            continue
        try:
            o[k] = max(0.0, min(6.0, float(v)))
        except (TypeError, ValueError):
            pass
    if isinstance(lights, dict):
        got = {k: max(0.0, min(4.0, float(v))) for k, v in lights.items()
               if k in ("key", "fill", "rim") and isinstance(v, (int, float))}
        if got:
            o["lights"] = got
    return o


def _look_state(e: dict, given: dict) -> dict:
    """This session's look after the call's changes, kept per project like the reference."""
    cur = dict(e.get("look") or {})
    cur.update(given or {})
    e["look"] = cur
    return cur


# THE BACKDROP AND THE PICTURE SIZE ARE STICKY TOO. Every body defaulted them to 640x480 on
# #1a1e26, so a call that left them out reset them: the grey an agent set on its first forge
# came back dark after the next glb or compare, and a look after that was judged on the wrong
# backdrop. Found by the cartoon-tree agents, 2026-09-23. 0 and "" now mean "the session's".
_CANVAS_DEFAULT = {"width": 640, "height": 480, "background": "#1a1e26"}


def _canvas(e: dict, width=0, height=0, background: str = "", remember: bool = True) -> tuple:
    """(width, height, background) for this call: what it names, else what the session set."""
    cur = dict(_CANVAS_DEFAULT, **(e.get("canvas") or {}))
    try:
        if int(width or 0) > 0:
            cur["width"] = int(width)
        if int(height or 0) > 0:
            cur["height"] = int(height)
    except (TypeError, ValueError):
        pass
    if str(background or "").strip():
        cur["background"] = str(background).strip()
    if remember:
        e["canvas"] = cur
    return int(cur["width"]), int(cur["height"]), str(cur["background"])


@_settles
def forge(project: str, js: str, views: Optional[list] = None, width: int = 0,
          height: int = 0, background: str = "", ground: bool = False,
          engine: str = "", clear: bool = True, margin: float = 0.0,
          label: str = "", quality: str = "", sky: bool = False,
          variants: Optional[list] = None, turntable: int = 0,
          passes: Optional[list] = None, ref: str = "",
          focus: Optional[list] = None, tags: Optional[list] = None, category: str = "",
          numbers: bool = False, edits: Optional[list] = None,
          detail=None, detail_views: Optional[list] = None,
          ortho=None, light: str = "", env=None, ambient=None, exposure=None,
          lights: Optional[dict] = None, shadows=None, ref_mask: str = "", ref_crop: str = "") -> dict:
    """Run code that makes an asset, and hand back a picture of what it made.

    This is the loop the Blender MCP gives a modelling agent, for a project whose assets are
    written instead of modelled: run a script, look at the result, change it, look again. The
    parts an agent should never have to write are the parts that make the picture worth anything
    — a neutral backdrop, a three-point rig, and a camera framed on the subject's bounding box so
    the same code frames identically every run and two versions can actually be compared.

    It builds with the project's OWN engine, imported from the module URL the page already
    loaded, so what is judged is what will ship. `import()` works inside the code, which is the
    part that matters most: an agent can preview the game's real `buildMeshes()` rather than a
    copy of it that has already drifted.
    """
    bad = _guard(project, forge=True)
    if bad:
        return bad
    if not str(js).strip():
        return {"ok": False, "error": "nothing to build"}
    # A 35% margin was a third of every panel spent on empty backdrop, which is a third of
    # the resolution the subject could have had. The framing loop below already widens
    # whenever the subject touches the border, so a tight default cannot clip anything.
    margin = float(margin or _engine_pref("forge_margin") or 1.06)
    want = [w for w in (_view_ok(v) for v in
                        (views or _engine_pref("forge_views") or ["3q"])) if w] or ["3q"]
    view_notes = _view_notes(views)
    variant_list = [v for v in (variants or []) if isinstance(v, dict)]
    pass_list = [p for p in (passes or []) if p in _PASSES]
    focus_list = [str(f).strip() for f in (focus or []) if str(f).strip()][:_MAX_PANELS]
    # Grab a part by name and move it, with no rebuild and nothing written to disk. See
    # `__forge.apply` for the shape of one edit.
    edit_list = [e for e in (edits or []) if isinstance(e, dict) and e.get("target")][:40]
    turn_steps = max(0, min(_MAX_PANELS, int(turntable or 0)))
    if turn_steps == 1:
        turn_steps = 0                      # one step is just a view; say so by ignoring it
    if variant_list:
        # Variants own the sheet: a grid of parameter sets crossed with a grid of angles is
        # unreadable at any panel size worth rendering.
        variant_list = variant_list[:_MAX_PANELS]
        if len(want) > 1:
            view_notes.append("VARIANTS: one view per variants call - drew %s; dropped %s. Call "
                              "again for each view." % (want[0], ", ".join(want[1:])))
        want, turn_steps, pass_list = want[:1], 0, []
    elif turn_steps:
        pass_list = pass_list[:max(0, _MAX_PANELS - turn_steps)]
    else:
        want = want[:_MAX_PANELS]
        pass_list = pass_list[:max(0, _MAX_PANELS - len(want))]
    _doing(project, "building", ", ".join(want) + (" · %d edits" % len(edit_list) if edit_list else ""),
           label or "")
    e = _entry(project)
    # Never opened on the live link — or the live link is off, which on a fresh install it is: the
    # forge opens the project's own page itself, because that page is where the engine comes from.
    # The forge's switch is the permission here, not the live link's.
    want_url = ""
    if _origin_gone(e):
        e["origin_restarted"] = e.get("url")
        e["url"] = ""
        view_notes.insert(0, "SERVER: the page server had stopped (%s); the Studio started it again and reloaded the page, so anything built before is gone - build again." % e.get("origin_restarted"))
    if not e.get("url"):
        try:
            origin, how = origin_for(Path(project), start_dev=True)
            want_url = origin.rstrip("/") + "/"
            if how:
                e["how"] = how
        except Exception as ex:
            return {"ok": False, "error": f"could not find a way to serve the project: {ex}"}
    if ref_mask or ref_crop:
        # Remembered here, crop and all — and `ref` is spent, because reading it again below
        # would set the picture a second time and throw the crop away.
        reference(project, ref, ref_mask, ref_crop)
        ref = ""
    width, height, background = _canvas(e, width, height, background)
    look_now = _look_state(e, _studio_opts(ortho, light, env, ambient, exposure, lights, shadows))
    opts = json.dumps(dict({"width": int(width), "height": int(height), "background": background,
                            "ground": bool(ground), "engine": engine or "", "sky": bool(sky)},
                           **look_now))
    # Resolved BEFORE the browser is touched, because it decides whether the extra silhouette
    # capture is worth taking. No reference, no score, nothing to capture.
    # `numbers` used to skip it, which made the cheap half of the loop the one call that could
    # not say whether the last edit helped. The capture is offscreen and costs no image tokens,
    # so the mode an agent repeats after every edit is exactly the mode that needs it.
    score_ref = "" if _ref_off(ref) else (
        (reference(project, ref) if ref else reference(project)).get("reference") or "")
    # The detail review holds close-ups against the reference whatever the mode, numbers
    # included — an upside-down face is a number worth having without a sheet.
    det_explicit, det_mode = _detail_mode(detail)
    det_ref = score_ref or ("" if not det_mode or _ref_off(ref) else
                            ((reference(project, ref) if ref else reference(project)).get("reference") or ""))
    # Destructured, not injected with `new Function`: a page with a content-security policy
    # blocks the latter, and the names an agent writes against have to be plain identifiers.
    body = ("(async()=>{const __c=__forge.ctx();"
            "const {pc,THREE,engine,app,device,renderer,scene,camera,root,forge,add,clear,log,"
            "params}=__c;"
            "const __m=__live.mark();"
            "let __v,__e;try{__v=await (async()=>{%s})();}catch(err){__e=String(err&&err.stack||err);}"
            "return JSON.stringify({v:__live.cap(__v,4,%d),e:__e,c:__live.since(__m)});})()"
            "\n//# sourceURL=studio-forge.js"
            % (js, _MAX_VALUE))
    # What the bench held before this call - put back if a heal has to reload the tab.
    bench_before = "" if clear else session_code(project)
    heal: dict = {"project": project}

    async def go():
        ws, live = await _session(e, want_url=want_url, wait_ms=1500 if want_url else 0)
        try:
            if want_url:
                await _bridge(live, e)
                e["url"] = want_url
            await _forge_script(live)
            built = await live.ask("__forge.ensure(%s)" % opts)
            if not (built or {}).get("ok"):
                return {"built": built}
            # WHERE EACH PART LANDED IN THE FRAME, filled in while the first view's camera is
            # still set. A screen position measured after the camera has moved on describes a
            # picture nobody was shown.
            placement: dict = {}

            async def put_bench_back():
                # A reload threw the bench away, and `clear:false` builds ON it: the parts earlier
                # calls added go back first, or this part would be judged alone.
                if bench_before.strip():
                    await live.raw(_forge_run_js(bench_before))

            async def run_once(params: Optional[dict]) -> dict:
                async def attempt():
                    if clear:
                        await live.raw("__forge.clear()")
                    await live.raw("__forge.params = %s" % json.dumps(params or {}))
                    try:
                        got = json.loads(await live.raw(body) or "{}")
                    except Exception as ex:
                        got = {"e": str(ex)[:600]}
                    return got, got.get("e") or ""
                # A module that will not import is healed here, before anything is measured.
                got_run = await _healing(live, e, opts, attempt, heal,
                                         None if clear else put_bench_back)
                # AFTER the build, BEFORE the camera. An edit is a nudge to what the code made,
                # so it has to happen once the code has made it, and it has to be in place before
                # anything is measured or framed — otherwise the numbers describe the asset the
                # agent has just decided to change.
                if edit_list:
                    got_run["edits"] = await live.ask("__forge.apply(%s)" % json.dumps(edit_list),
                                                      depth=6)
                return got_run

            shots, ran, stats = [], {}, {}
            detail_got = None
            # The scoring silhouette. Never added to `shots`, so it never reaches the sheet.
            score_shot = ""

            if variant_list:
                # Two passes, and the first one is why this is worth having.
                #
                # Framed independently, every variant fills its panel — so a blade 60% wider is
                # DRAWN THE SAME SIZE as a narrow one and the sheet compares nothing. The framing
                # cancels out exactly the quantity being swept. Measured on the first real sweep:
                # width 0.10 and 0.24 looked the same width on the sheet.
                #
                # So: build each variant and record its radius, take the largest, then rebuild and
                # render them all at that one distance. A build is a few milliseconds; a sheet
                # that silently compares nothing costs a wrong decision.
                radii = []
                for params in variant_list:
                    await run_once(params)
                    b = await live.ask("__forge.bounds()") or {}
                    radii.append(float(b.get("radius") or 0) if isinstance(b, dict) else 0.0)
                hold = max([r for r in radii if r > 0] or [0.0])
                for i, params in enumerate(variant_list):
                    ran = await run_once(params)
                    img = await live.raw("__forge.view(%s,%s,%s)"
                                         % (json.dumps(want[0]), float(margin), hold))
                    shots.append((_label_params(params, i), img))
                    if i == 0:
                        stats = await live.ask("__forge.stats()", depth=8) or {}
                if hold:
                    # Say it, so a reader knows the panels are comparable and why.
                    stats["framing"] = "held at radius %.3f for all %d variants" % (
                        hold, len(variant_list))
            else:
                ran = await run_once(None)
                if turn_steps:
                    # Frame ONCE, then rotate the subject. The fit comes from the bounding
                    # sphere, which does not change as it turns, so no step clips or drifts.
                    first = await live.raw("__forge.view(%s,%s)"
                                           % (json.dumps(want[0]), float(margin)))
                    shots.append(("0°", first))
                    for i in range(1, turn_steps):
                        deg = round(360.0 * i / turn_steps)
                        await live.raw("__forge.turn(%d)" % deg)
                        shots.append(("%d°" % deg, await live.raw("__forge.shot()")))
                    await live.raw("__forge.turn(0)")
                else:
                    for v in want:
                        shots.append((v, await live.raw("__forge.view(%s,%s)"
                                                        % (json.dumps(v), float(margin)))))
                        if v == want[0] and not placement:
                            placement.update({
                                "view": v,
                                "parts": await live.ask("__forge.where()", depth=6) or [],
                            })
                stats = await live.ask("__forge.stats()", depth=8) or {}
                if placement:
                    stats["placement"] = dict(placement)
                # The passes reuse whatever framing the last view left, so they line up with it.
                for p in pass_list:
                    if await live.ask("__forge.setPass(%s)" % json.dumps(p)):
                        shots.append((p, await live.raw("__forge.shot()")))
                await live.raw("__forge.clearPass()")
                # ONE MORE FRAME, NEVER SHOWN: the silhouette the score is measured on.
                #
                # `aim` sweeps with no sky, no floor and the silhouette override, and reports an
                # overlap from that. A render reports one taken off the lit colour picture, where
                # the subject has to be told apart from its own backdrop by brightness. The two
                # numbers then disagree — measured at 0.899 against 0.916 on the same chest at the
                # same angle — and an agent that has both cannot tell which to believe. One of
                # them lost a shot to exactly this.
                #
                # So the render's number now comes from the same kind of picture aim's does, at
                # the SAME camera the placement was measured through, and the two agree by
                # construction. It costs one offscreen capture and no image tokens.
                # THE PASS GOES ON FIRST, THEN THE FRAMING — the order `aim` uses, and the order
                # matters. `__forge.view()` does not only fit the bounding sphere: it renders,
                # looks at the pixels, and backs off if the subject touches the border, because a
                # skinned mesh or a displacing shader draws outside its own box. That check reads
                # the drawn image, so framing under the beauty pass and then swapping to the
                # silhouette lands the camera somewhere `aim` never puts it. Measured on one
                # chest: the bottom band came out 215px against aim's 263.
                if score_ref:
                    if await live.ask("__forge.setPass('silhouette')"):
                        score_shot = await live.raw("__forge.view(%s,%s)"
                                                    % (json.dumps(want[0]), float(margin)))
                    await live.raw("__forge.clearPass()")
                # LAST, deliberately. `stats` is already taken, so the pixel sizes it reports are
                # the ones from the whole-subject framing -- which is the framing whose blind
                # spots the findings are about. Measuring them after a close-up would report that
                # everything is perfectly visible, which is true and useless.
                for fname in focus_list:
                    img = await live.raw("__forge.view(%s,%s,0,%s)"
                                         % (json.dumps(want[0]), float(margin),
                                            json.dumps(fname)))
                    shots.append(("focus: %s @ %s" % (fname, want[0]), img))
                # THE DETAIL REVIEW, last for the same reason: the part sizes above belong to the
                # whole-subject framing. See live_detail for why it exists.
                if det_mode:
                    detail_got = await _detail_pass(live, det_explicit, det_mode, tags, det_ref,
                                                    want[0], float(margin), detail_views)

            if not stats:
                stats = await live.ask("__forge.stats()", depth=8) or {}
            nodes = []
            if numbers:
                # Only in the cheap mode, and deliberately. This is the list an agent picks a
                # target from, and it is worth its few hundred tokens exactly when there is no
                # picture to point at.
                nodes = await live.ask("__forge.nodes(60)", depth=6) or []
            return {"built": built, "ran": ran, "shots": shots, "stats": stats, "nodes": nodes,
                    "score_shot": score_shot, "detail": detail_got,
                    "health": await live.ask("__forge.health()", depth=4) or [],
                    "holes": (await live.ask("__forge.holes()", depth=6) or {}) if _checks_on() else {},
                    "symmetry": (await live.ask("__forge.symmetry()", depth=5) or {}) if _checks_on() else {},
                    "gaps": (await live.ask("__forge.gaps()", depth=5) or {}) if _checks_on() else {},
                    # Where the camera finished, so a window that is following can stand there.
                    "at": await live.ask("__forge.at()", depth=4) or {}}
        finally:
            await ws.close()

    try:
        got = _run(go)
    except Exception as ex:
        return {"ok": False, "error": str(ex).strip()[:1200]}
    built = got.get("built") or {}
    if not built.get("ok"):
        return {"ok": False,
                "error": built.get("error") or ("the forge could not start: %s"
                                                % json.dumps(built)[:400])}

    ran = got.get("ran") or {}
    stats = got.get("stats") or {}
    res: dict = {"ok": True, "engine": built.get("engine"),
                 "engine_url": built.get("engine_url", ""), "stats": stats,
                 # What the studio is set to now: the rig, the exposure and the projection. An
                 # agent that asks for a change gets the answer back instead of guessing.
                 "look": built.get("look") or {},
                 # Where the camera ended up, so the next call can orbit from here rather than
                 # start again from a preset.
                 "at": got.get("at") or {}}
    if focus_list:
        res["focus"] = focus_list
    healed = _heal_report(heal)
    if healed:
        res["heal"] = healed
    if ran.get("v") is not None:
        res["value"] = ran["v"]
    if ran.get("c"):
        # ERRORS FIRST, and say what was cut. Forty records of `log` used to be able to push the
        # one line that says why the build failed off the end of the list, which made a run that
        # reported its own cause look like a run with no explanation. `sorted` is stable, so
        # within a rank the page's own order is kept.
        rank = {"error": 0, "promise": 0, "resource": 0, "net": 0, "warn": 1}
        rows = sorted(ran["c"], key=lambda r: rank.get(r.get("kind"), 2))
        res["console"] = rows[:40]
        counts: dict = {}
        for r in ran["c"]:
            counts[r.get("kind", "?")] = counts.get(r.get("kind", "?"), 0) + 1
        res["console_counts"] = counts
        if len(rows) > 40:
            res["console_more"] = len(rows) - 40
    if ran.get("edits"):
        # Echoed, never saved. The transform an agent liked belongs in the builder that made the
        # asset; a viewer that quietly became the source of truth would be a worse tool.
        res["edits"] = ran["edits"]
    if ran.get("e"):
        # The code threw. Say so and still return the sheet — a half-built asset in the picture
        # is usually the fastest way to see WHERE it threw.
        res["ok"] = False
        res["error"] = ran["e"][:1200]

    if numbers:
        # THE CHEAP HALF OF THE LOOP. A contact sheet costs a vision model about 2,300 tokens
        # every time it is read; these lines cost a few hundred. So an agent can measure after
        # every edit and spend a look only when the numbers stop moving, which is the difference
        # between eleven looks and three.
        got_ref = reference(project, ref) if ref else reference(project)
        res["findings"] = _findings(stats, bool(got_ref.get("reference")), focus_list,
                                    ref_off=_ref_off(ref))
        res["findings"][0:0] = view_notes
        res["nodes"] = got.get("nodes") or []
        place = (stats.get("placement") or {})
        if place:
            res["placement"] = place
            pf = _placement_finding(place)
            if pf:
                res["findings"].insert(0, pf)
        # THE SCORE, IN THE CHEAP HALF TOO. The same silhouette, scorer and reference as the
        # sheet path, so a numbers run and a look run cannot disagree about the outline.
        sil = _decode_shot(got.get("score_shot") or "")
        if score_ref and sil is not None:
            cmp = _score_against(score_ref, sil, want[0], drawn=True,
                                 ref_mask=got_ref.get("mask") or "")
            if cmp:
                res["score"] = cmp
                for line in reversed(_score_finding(
                        cmp, (_last_run(project, label).get("score") or {}))):
                    res["findings"].insert(0, line)
                band = _band_finding(cmp)
                if band:
                    res["findings"].append(band)
                # THE DARKS, not only the outline - off the lit frame of the same first view,
                # which was rendered for the numbers too and simply never shown.
                shots0 = got.get("shots") or []
                con = {} if not _checks_on() else _contrast(_decode_shot(shots0[0][1]) if shots0 else None, score_ref,
                                got_ref.get("mask") or "")
                if con:
                    res["contrast"] = con
                    cl = _contrast_finding(con)
                    if cl:
                        res["findings"].append(cl)
        res["findings"].extend(_health_finding(got.get("health")))
        res["findings"].extend(_hole_finding(got.get("holes")))
        _hole_summary(res, got.get("holes"))
        res["findings"].extend(_symmetry_finding(got.get("symmetry")))
        res["findings"].extend(_gap_finding(got.get("gaps")))
        res["numbers_only"] = True
        _detail_out(project, got.get("detail"), built.get("engine", ""), label, res)
        # Recorded WITHOUT a sheet, so a person watching sees this run happen. The code is here,
        # which is all the Engine window needs to rebuild it in a viewport that can be orbited.
        try:
            from . import engine as _engine
            gid = _engine.record(project, label or "", js, built.get("engine", ""), stats, want,
                                 "", bool(res.get("ok")), str(res.get("error", "")),
                                 built.get("engine_url", ""), tags=tags, category=category,
                                 extra={"numbers_only": True, "asked_views": want,
                                        "score": res.get("score") or {},
                                        "detail": res.get("detail") or [],
                                        "detail_sheet": res.get("detail_sheet") or "",
                                        "edits": ran.get("edits") or [], "margin": margin})
            if gid:
                res["generation"] = gid
        except Exception:
            pass
        _bench_put(project, js, clear, {}, stats, label)
        _done(project, "measured", "%s tris · %s meshes"
              % (stats.get("triangles", "?"), stats.get("meshes", "?")))
        # LAST, so nothing inserted at the front afterwards pushes it down: a build that had to
        # reload the tab, or could not import at all, is the first thing to read.
        res["findings"][:0] = _heal_finding(healed)
        return res

    frames, labels = [], []
    for name, data in (got.get("shots") or []):
        im = _decode_shot(data)
        if im is None:
            res.setdefault("notes", []).append("view %s: %s" % (name, str(data)[:160]))
            continue
        frames.append(im)
        labels.append(name)
    # THE REFERENCE GOES FIRST, in the same picture. Comparing a render on screen against an
    # image read twenty tool calls ago is comparing against a memory, and a memory is exactly
    # where proportions, colour and pose quietly drift.
    has_ref = False
    if frames:
        rf, why = (None, "") if _ref_off(ref) else \
            _ref_frame(project, ref, frames[0].size)
        if rf is not None:
            frames.insert(0, rf)
            labels.insert(0, "REFERENCE (the target)")
            has_ref = True
        elif why:
            res.setdefault("notes", []).append(why)
    res["findings"] = _findings(stats, has_ref, focus_list)
    res["findings"][0:0] = view_notes
    # THE SCORE, ON EVERY SHOT — not only in `aim`. Read before the picture is opened, and the
    # one number that says whether the last change helped or hurt.
    place = (stats.get("placement") or {})
    if place:
        res["placement"] = place
    if has_ref and len(frames) > 1:
        # frames[0] IS THE REFERENCE — it was inserted at the front three lines ago. Scoring it
        # against itself returns a perfect 1.00 and means nothing, which is the kind of number
        # that is worse than none at all. The first RENDER is frames[1].
        ref_used = score_ref
        if ref_used:
            # The hidden silhouette when there is one, the lit frame only as a fallback — a page
            # whose engine cannot do an override pass still gets a number, and `score.from` says
            # which it was.
            sil = _decode_shot(got.get("score_shot") or "")
            cmp = _score_against(ref_used, sil if sil is not None else frames[1],
                                 want[0], drawn=sil is not None,
                                 ref_mask=reference(project).get("mask") or "")
            if cmp:
                res["score"] = cmp
                for line in reversed(_score_finding(cmp, (_last_run(project, label).get("score") or {}))):
                    res["findings"].insert(0, line)
                band = _band_finding(cmp)
                if band:
                    res["findings"].append(band)
                # THE DARKS, not only the outline: the lit frame of the view that was scored.
                con = _contrast(frames[1], ref_used, reference(project).get("mask") or "") if _checks_on() else {}
                if con:
                    res["contrast"] = con
                    cl = _contrast_finding(con)
                    if cl:
                        res["findings"].append(cl)
    res["findings"].extend(_health_finding(got.get("health")))
    res["findings"].extend(_hole_finding(got.get("holes")))
    _hole_summary(res, got.get("holes"))
    res["findings"].extend(_symmetry_finding(got.get("symmetry")))
    res["findings"].extend(_gap_finding(got.get("gaps")))
    if place:
        # WHAT MOVED, first — ahead of everything else in the list. It is the only line that says
        # "you just made this worse", and it is worth nothing if it is read fourth.
        drift = _drift_finding(place, (_last_run(project, label).get("placement") or {}))
        if drift:
            res["drift"] = drift
            res["findings"].insert(0, drift)
        pf = _placement_finding(place)
        if pf:
            res["findings"].append(pf)
    _detail_out(project, got.get("detail"), built.get("engine", ""), label, res)

    if frames:
        from . import review as _review
        want_q = str(quality or _engine_pref("forge_quality") or settings.get("cc_review_quality") or "normal").lower()
        budget = _review.QUALITY.get(want_q, _review.QUALITY["normal"])[0]
        title = label or "forge"
        sub = "%s · %s tris · %s materials" % (built.get("engine", "?"),
                                               stats.get("triangles", "?"),
                                               stats.get("materials", "?"))
        if stats.get("flat"):
            sub = "%s · flat image %sx%s" % (built.get("engine", "?"),
                                             *(stats.get("image") or ["?", "?"]))
        sheet = _review._sheet(frames, labels, title, sub, budget)
        out = _LIVE_DIR / _slug(project) / ("forge-%d.png" % int(time.time() * 1000))
        out.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(out)
        res["sheet"] = str(out)
        res["views"] = labels
        # The code, beside the picture. A sheet cannot be orbited and two of them cannot be
        # compared except by eye; with the code kept, the engine window can re-run any past
        # generation in its own renderer and turn it around. Best-effort on purpose — a forge
        # that rendered correctly must not fail because the archive could not be written.
        try:
            from . import engine as _engine
            gid = _engine.record(project, label or "", js, built.get("engine", ""), stats, labels,
                                 str(out), bool(res.get("ok")), str(res.get("error", "")),
                                 built.get("engine_url", ""), tags=tags, category=category,
                                 extra={"asked_views": want, "edits": ran.get("edits") or [],
                                        "detail": res.get("detail") or [],
                                        "detail_sheet": res.get("detail_sheet") or "",
                                        "margin": margin, "score": res.get("score") or {},
                                        "placement": res.get("placement") or {}})
            if gid:
                res["generation"] = gid
        except Exception:
            pass
    else:
        res.setdefault("notes", []).append("no view rendered")
    _bench_put(project, js, clear, got.get("at") or {}, stats, label)
    _done(project, "rendered", ", ".join(labels[1:] if has_ref else labels)[:120])
    if not stats.get("meshes") and not stats.get("flat") and res.get("ok"):
        res["hint"] = ("nothing was added to the forge. Pass what you built to `add(...)` — the "
                       "code runs with add, root, scene, camera and either THREE or pc in scope.")
    res.setdefault("findings", [])[:0] = _heal_finding(healed)
    return res


def _run_pictures(e, want_url: str, opts: str, jobs: list) -> dict:
    """One visit, one picture each. `jobs` is [(record path, code)]."""

    async def go():
        ws, live = await _session(e, want_url=want_url, wait_ms=1500 if want_url else 0)
        try:
            if want_url:
                await _bridge(live, e)
                e["url"] = want_url
            await _forge_script(live)
            built = await live.ask("__forge.ensure(%s)" % opts)
            if not (built or {}).get("ok"):
                return {"error": (built or {}).get("error") or "the forge would not start"}
            out = []
            heal: dict = {}
            for rec_path, code in jobs:
                body = ("(async()=>{const __c=__forge.ctx();const {pc,THREE,engine,app,device,"
                        "renderer,scene,camera,root,forge,add,clear,log,params}=__c;"
                        "let __e;try{await (async()=>{%s})();}catch(err){__e=String(err&&err."
                        "message||err);}return JSON.stringify({e:__e});})()" % code)

                async def attempt(body=body):
                    await live.raw("__forge.clear()")
                    await live.raw("__forge.params = {}")
                    try:
                        err = (json.loads(await live.raw(body) or "{}") or {}).get("e") or ""
                    except Exception as ex:
                        err = str(ex)[:200]
                    return err, err
                # One heal per visit: a stale tab fails every record the same way.
                err = await _healing(live, e, opts, attempt, heal)
                img = await live.raw("__forge.view('3q',1.06)")
                out.append((rec_path, img, err))
            return {"shots": out, "heal": _heal_report(heal)}
        finally:
            await ws.close()

    try:
        return _run(go)
    except Exception as ex:
        return {"error": str(ex)[:300]}


def _count_tries(part: list, why: str) -> None:
    """Remember that a whole batch failed, so the next pass gets past it."""
    for rec_path, rec, _code in part:
        rec["picture_tries"] = int(rec.get("picture_tries") or 0) + 1
        rec["picture_why"] = why[:200]
        try:
            tmp = rec_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
            os.replace(tmp, rec_path)
        except Exception:
            pass


def pictures(project: str = "", limit: int = 40, size: int = 384) -> dict:
    """Draw and keep a picture for every recorded run that has code but no sheet.

    Safe to call again: a run that already has a picture is skipped, and a run whose code draws
    nothing is recorded as such rather than retried forever.
    """
    bad = _guard(project or str(Path.cwd()), forge=True)
    if bad:
        return bad
    todo, seen = [], 0
    roots = [_LIVE_DIR / _slug(project)] if project else sorted(_LIVE_DIR.glob("*"))
    for folder in roots:
        if not folder.is_dir():
            continue
        for rec_path in sorted(folder.glob("forge-*.json")):
            if len(todo) >= max(1, min(200, int(limit or 40))):
                break
            try:
                rec = json.loads(rec_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            seen += 1
            png = rec_path.with_suffix(".png")
            if png.is_file() or (rec.get("sheet") and Path(rec["sheet"]).is_file()):
                continue
            if rec.get("no_picture"):        # already tried, and it drew nothing
                continue
            # A project whose page cannot serve an engine fails for every record it holds. Two
            # attempts and it is left alone, or the pass spends every run on the same batch and
            # never reaches the ones it could draw.
            if int(rec.get("picture_tries") or 0) >= 2:
                continue
            code = str(rec.get("code") or "").strip()
            if code:
                todo.append((rec_path, rec, code))
    if not todo:
        return {"ok": True, "drawn": 0, "looked_at": seen, "left": 0}

    # Grouped by the project each record belongs to: a record made against one game's page cannot
    # be rebuilt on another's, for the same reason asset thumbnails are grouped this way.
    by_project: dict = {}
    for rec_path, rec, code in todo:
        by_project.setdefault(str(rec.get("project") or project or ""), []).append(
            (rec_path, rec, code))

    opts = json.dumps({"width": int(size), "height": int(size), "background": "#161a22",
                       "ground": False, "engine": "", "sky": False})
    drawn, blank, errs, heals = 0, 0, {}, {}
    from PIL import Image
    import io as _io

    for proj, part in by_project.items():
        proj = proj or project
        if not proj:
            continue
        e = _entry(proj)
        want_url = ""
        if not e.get("url"):
            try:
                origin, how = origin_for(Path(proj), start_dev=True)
                want_url = origin.rstrip("/") + "/"
                if how:
                    e["how"] = how
            except Exception as ex:
                errs[proj] = "no way to serve it: %s" % str(ex)[:120]
                _count_tries(part, errs[proj])
                continue
        got = _run_pictures(e, want_url, opts, [(rp, c) for rp, _r, c in part])
        if got.get("heal"):
            heals[proj] = got["heal"]
        if got.get("error"):
            errs[proj] = got["error"][:200]
            _count_tries(part, errs[proj])
            continue
        by_path = {rp: (rp, r, c) for rp, r, c in part}
        for rec_path, img, err in got.get("shots") or []:
            rec = by_path[rec_path][1]
            ok_img = isinstance(img, str) and img.startswith("data:image")
            im = None
            if ok_img:
                try:
                    im = Image.open(_io.BytesIO(base64.b64decode(img.split(",", 1)[1]))).convert("RGB")
                except Exception:
                    im = None
            if im is None or _ink(im) < 0.004:
                # SAID ONCE, not retried forever. A measuring run that moved a part and drew
                # nothing new is a legitimate record with nothing to show.
                rec["no_picture"] = err or "this run drew nothing"
                blank += 1
            else:
                out = rec_path.with_suffix(".png")
                im.save(out)
                rec["sheet"] = str(out)
                rec["views"] = ["3q"]
                rec["drawn_later"] = True
                drawn += 1
            try:
                tmp = rec_path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
                os.replace(tmp, rec_path)
            except Exception:
                pass
    # Nothing to invalidate: `history()` reads the records off disk on every call, so a picture
    # written here is in the strip on its next poll.
    out = {"ok": True, "drawn": drawn, "drew_nothing": blank, "looked_at": seen, "errors": errs}
    if heals:
        out["heal"] = heals
    return out


# ---------------------------------------------------------------------------
# Many calls, one request
#
# Unity's MCP has `batch_execute` and reports "dramatically better performance". They are right,
# and the reason is not the websocket — it is the AGENT. Build a part, measure it, nudge it, look:
# four HTTP calls is four turns of latency in the loop that matters, and the loop runs dozens of
# times per asset.
#
# Deliberately a plain loop over the functions that already exist rather than a refactor that
# shares one socket. Each call still opens its own short-lived connection to the same warm tab —
# tens of milliseconds — while what it saves is whole round trips through the model. Sharing the
# socket would mean touching fourteen call sites for a fraction of the win.

_BATCH: dict = {}


def _batch_ops() -> dict:
    """Built once, lazily, because these functions are defined further down this module."""
    global _BATCH
    if not _BATCH:
        _BATCH = {
            "forge": forge, "look": look, "aim": aim, "eval": evaluate,
            "scene": scene, "find": find, "console": console, "perf": perf,
            "bench": bench, "pictures": pictures,
        }
        # The scene calls: list, move, place, read back. `edit` and `place` take a body, so the
        # call's own fields become it — the same shape the HTTP routes take. A batch is a script:
        # its numbers fast, and a picture only where a call asks for one (`look`), so an edit in a
        # batch does not follow the scene_look setting the way a single call does.
        def _body(project, kw):
            body = {"look": False}
            body.update(kw)
            body["project"] = project
            return body
        try:
            from . import live_scene as _ls
            _BATCH.update({
                "objects": _ls.objects,
                "edit": lambda project, **kw: _ls.edit(project, _body(project, kw)),
                "place": lambda project, **kw: _ls.place(project, _body(project, kw)),
                "edits": lambda project, **kw: _ls.edits(project),
            })
        except Exception:                                    # noqa: BLE001
            pass
        # The camera calls: a framed shot of one thing, and a strip of frames over real time.
        try:
            from . import live_view as _lv
            _BATCH.update({"shot": _lv.shot, "watch": _lv.watch})
        except Exception:                                    # noqa: BLE001
            pass
    return _BATCH


def batch(project: str, calls: list, stop_on_error: bool = True) -> dict:
    """Run several live calls in order, on the same tab, and hand back every answer.

    Each entry is `{"op": "forge", ...the same arguments that op takes}`. `project` is supplied
    once and never per call. A call that throws is reported in place rather than taking the whole
    batch down, and by default the run stops there — a `look` after a failed `forge` photographs
    the previous asset and reads as though nothing went wrong, which is worse than stopping.
    """
    bad = _guard(project)
    if bad:
        return bad
    if not isinstance(calls, list) or not calls:
        return {"ok": False, "error": "give me a list of calls"}
    ops = _batch_ops()
    if len(calls) > 20:
        return {"ok": False, "error": "20 calls at a time is plenty; got %d" % len(calls)}

    out: list = []
    stopped = ""
    for i, c in enumerate(calls):
        if not isinstance(c, dict):
            out.append({"op": "?", "ok": False, "error": "each call must be an object"})
            break
        op = str(c.get("op") or "").strip()
        fn = ops.get(op)
        if not fn:
            out.append({"op": op, "ok": False,
                        "error": "no such op; try " + ", ".join(sorted(ops))})
            stopped = "call %d: unknown op %r" % (i + 1, op)
            break
        kw = {k: v for k, v in c.items() if k != "op"}
        try:
            r = fn(project=project, **kw)
        except TypeError as ex:
            # Almost always a wrong argument name, which is worth saying precisely: the whole
            # point of a batch is that the agent is not watching each call go by.
            r = {"ok": False, "error": "%s does not take those arguments: %s" % (op, ex)}
        except Exception as ex:                              # noqa: BLE001
            r = {"ok": False, "error": "%s: %s" % (type(ex).__name__, str(ex)[:400])}
        r = dict(r) if isinstance(r, dict) else {"ok": True, "value": r}
        r["op"] = op
        out.append(r)
        if stop_on_error and not r.get("ok", True):
            stopped = "call %d (%s) failed" % (i + 1, op)
            break

    return {"ok": not stopped, "ran": len(out), "asked": len(calls),
            "stopped": stopped, "results": out}


# ---------------------------------------------------------------------------
# The bench: what the forge page is holding right now
# ---------------------------------------------------------------------------
#
# Kept in memory beside the tab, not on disk. It describes a browser that may be closed at any
# moment, and a stale copy of that on disk would be worse than none: a window would mirror a
# scene that no longer exists and show the user a lie.

_bench: dict = {}
_BENCH_MAX = 60_000          # a session's code, capped, so an append loop cannot grow forever


def _scoped(code: str) -> str:
    """One forge call's code, in a block of its own."""
    code = str(code or "").strip()
    return ("{\n" + code + "\n}") if code else ""


def _bench_put(project: str, code: str, clear: bool, at: dict, stats: dict, label: str,
               model: str = "") -> None:
    """Record what the page now holds. `clear=False` appends, because that is what it did.

    `model` is the absolute path of a GLB that /glb has just put on the bench."""
    k = key_for(project)
    # THE MODEL FILES, BESIDE THE CODE. A GLB on the bench left only a comment in `code`, so the
    # Engine window rebuilt an empty scene from it and the person watching saw nothing while the
    # agent judged its own export. The files are kept here and the window loads them itself. `v`
    # is the file's time, so a GLB written again under the same name is a new scene.
    v = 0
    if model:
        try:
            v = int(Path(model).stat().st_mtime)
        except OSError:
            pass
    with _lock:
        cur = _bench.get(k) or {}
        old = str(cur.get("code") or "")
        code = str(code or "")
        models = [] if clear else [dict(m) for m in (cur.get("models") or [])]
        if model:
            models = [m for m in models if m.get("path") != model]
            models.append({"name": Path(model).name, "path": model, "v": v})
        # EACH CHUNK IN ITS OWN BLOCK. The forge runs every call inside its own async function,
        # so two calls may both declare `const mat` and neither knows about the other. Pasted end
        # to end into one scope that is a SyntaxError, and a viewport rebuilding from it shows
        # nothing at all. Braces reproduce the scoping the forge already had, and `add` is still
        # in scope from outside.
        chunk = _scoped(code)
        if clear or not old:
            full = chunk
        elif chunk and chunk not in old:
            full = (old + "\n\n// ---- and then ----\n" + chunk)[-_BENCH_MAX:]
        else:
            full = old
        ident = (full, tuple((m["path"], m["v"]) for m in models)) if models else full
        _bench[k] = {
            "project": str(Path(project)),
            "code": full,
            "models": models,
            # A cheap identity for the scene. A mirror rebuilds when this changes and only then.
            "sig": "%d:%d" % (len(full), hash(ident) & 0xFFFFFFF),
            "at": dict(at or cur.get("at") or {}),
            "stats": {x: (stats or {}).get(x) for x in ("triangles", "meshes", "materials")},
            # WHICH ENGINE BUILT IT. A window that mirrors the bench has to build the same code,
            # and without this it guessed three — so every PlayCanvas bench opened in the Edit
            # tab as "could not load the project's own engine ... loaded, but it is not three".
            "engine": str((stats or {}).get("engine") or cur.get("engine") or ""),
            "label": str(label or cur.get("label") or ""),
            "ts": time.time(),
        }


def session_code(project: str) -> str:
    """Everything the page has been given since the last clear."""
    with _lock:
        return str((_bench.get(key_for(project)) or {}).get("code") or "")


def bench(project: str) -> dict:
    """What is on the bench, for a window that wants to mirror it.

    `open` says whether a tab is actually still there. Without it a window would happily follow a
    session that was reaped ten minutes ago.
    """
    # NO PROJECT MEANS "whichever one is being worked on". The Engine window opens on
    # "All projects", and a window that could only follow a project you had named followed nothing
    # at all in the one case a person is most likely to be watching.
    if not project:
        with _lock:
            newest = max(_bench.values(), key=lambda v: v.get("ts") or 0, default=None)
        if not newest:
            return {"ok": True, "open": False, "sig": "", "code": "", "at": {}, "stats": {}}
        project = str(newest.get("project") or "")
    bad = _guard(project, forge=True)
    if bad:
        return bad
    k = key_for(project)
    with _lock:
        b = dict(_bench.get(k) or {})
        e = _tabs.get(k)
    if not b:
        return {"ok": True, "open": False, "sig": "", "code": "", "at": {}, "stats": {}}
    b["open"] = bool(e and e.get("target"))
    b["ok"] = True
    return b


@_settles
def look(project: str, views: Optional[list] = None, orbit: Optional[list] = None,
         zoom: float = 0.0, focus: Optional[list] = None, edits: Optional[list] = None,
         turntable: int = 0, passes: Optional[list] = None, margin: float = 0.0,
         quality: str = "", label: str = "", ref: str = "", numbers: bool = False,
         tags: Optional[list] = None, category: str = "",
         detail=None, detail_views: Optional[list] = None,
         ortho=None, light: str = "", env=None, ambient=None, exposure=None,
         lights: Optional[dict] = None, shadows=None, ref_mask: str = "", ref_crop: str = "",
         sweep: int = 0, width: int = 0, height: int = 0, frames: bool = False,
         facts: bool = False, anchors: Optional[list] = None) -> dict:
    """Look again at what is already built. No rebuild, no page load, no code run.

    `anchors`, as `aim` takes them, solve the camera the reference was taken from first; with no
    `views` that solved view is the one photographed, and `solved` and `snap` come back beside it.

    WHY THIS EXISTS. `forge` clears the scene and re-runs the code on every call, so a second
    angle cost a second build — and an agent given one picture per build takes one picture and
    stops. Measured on the chest: a build-and-shoot is about four seconds, this is well under one.
    Cheap enough that "front, side, back and three-quarter, then a close-up of the hasp" is a
    thing an agent will actually do, which is the whole point: the next generation is better
    because the last one was looked at properly.

    Everything it needs already existed. The forge's tab survives between calls and the scene
    inside it survives with it; nothing here is new state, only the decision not to throw the
    scene away.

    `orbit` is [dAz, dEl] in degrees, added to wherever the camera is now — so "turn it a bit
    further round" needs no arithmetic and no knowledge of which preset it started from.
    """
    bad = _guard(project, forge=True)
    if bad:
        return bad
    e = _entry(project, create=False)
    if not e or not e.get("target"):
        return {"ok": False, "error": "nothing is open for this project. Build it once with "
                                      "/api/live/forge, then look at it as often as you like."}
    anchor_specs, anchor_bad = _anchor_specs(anchors) if anchors else ([], [])
    if anchors and len([s for s in anchor_specs if s["solve"]]) < _ANCHOR_MIN:
        return {"ok": False,
                "error": "ANCHORS: a camera needs at least %d anchors to solve from; got %d usable%s"
                         % (_ANCHOR_MIN, len([s for s in anchor_specs if s["solve"]]),
                            (" - " + "; ".join(anchor_bad[:4])) if anchor_bad else "")}
    if _mask_auto(ref_mask):
        ref_mask = ""                           # the ladder is the default; nothing to set
    if ref_mask or ref_crop:
        reference(project, ref, ref_mask, ref_crop)
        ref = ""                                # spent: reading it again would drop the crop
    sweep_n = max(0, min(5, int(sweep or 0)))
    # The rig only moves when this call asks it to. Looking again must not undo the last forge.
    given_look = _studio_opts(ortho, light, env, ambient, exposure, lights, shadows)
    look_now = _look_state(e, given_look) if given_look else dict(e.get("look") or {})
    margin = float(margin or _engine_pref("forge_margin") or 1.06)
    # `"edits": "reset"` PUTS THE BENCH BACK. Edits persisted for the life of the session with
    # nothing that could undo them, so a single exploratory nudge quietly changed every picture
    # and every score after it.
    revert = isinstance(edits, str) and str(edits).strip().lower() in ("reset", "revert", "clear")
    edit_list = [x for x in (edits or []) if isinstance(x, dict)] if not revert else []
    focus_list = [str(f) for f in (focus or []) if str(f).strip()][:4]
    pass_list = [p for p in (passes or []) if p in _PASSES]
    turn_steps = max(0, min(16, int(turntable or 0)))
    score_ref = "" if (numbers or _ref_off(ref)) else (
        (reference(project, ref) if ref else reference(project)).get("reference") or "")
    det_explicit, det_mode = _detail_mode(detail)
    det_ref = score_ref or ("" if not det_mode or _ref_off(ref) else
                            ((reference(project, ref) if ref else reference(project)).get("reference") or ""))

    ref_size = None
    if anchor_specs:
        try:
            from PIL import Image as _Img
            _rp = reference(project).get("reference") or ""
            if _rp:
                with _Img.open(_rp) as _im:
                    ref_size = _im.size
        except Exception:
            ref_size = None
    # The size is sticky: a look that names one keeps it, one that does not gets the session's -
    # so an aim sweep (420x420) never leaves the next sheet at a size nobody asked for.
    canvas = (_canvas(e, width, height, "")
              if (int(width or 0) or int(height or 0) or e.get("canvas")) else None)
    view_notes = _view_notes(views)
    applied: list = []
    _doing(project, "looking", label or "another angle")

    async def go():
        ws, live = await _session(e)
        try:
            if not await live.raw("!!window.__forge"):
                return {"gone": "the page has been reloaded since it was built"}
            from .live_forge import FORGE_VERSION
            if int(await live.raw("window.__forge.version || 0") or 0) != FORGE_VERSION:
                return {"gone": "the page is holding the forge script from before the Studio was "
                                "updated, and the scene lives inside it"}
            look_got = None
            reverted = None
            if revert:
                reverted = await live.raw("__forge.revert()")
            sized = None
            if canvas:
                sized = await live.ask("__forge.resize(%d,%d)" % (canvas[0], canvas[1]), depth=2)
            if given_look:
                look_got = await live.ask("__forge.studio(%s)" % json.dumps(look_now), depth=3)
            at = await live.ask("__forge.at()", depth=4) or {}
            stats = await live.ask("__forge.stats()", depth=8) or {}
            if not stats.get("meshes") and not stats.get("flat"):
                return {"gone": "the scene is empty"}

            # The angles to photograph. `views` wins; otherwise orbit or zoom from where the
            # camera is now, and with none of the three it is simply the same shot again.
            want = [w for w in (_view_ok(v) for v in (views or [])) if w]
            solved = None
            if anchor_specs:
                # Edits first: an anchor is measured on the asset as it will be photographed.
                if edit_list:
                    applied.extend(await live.ask("__forge.apply(%s)" % json.dumps(edit_list),
                                                  depth=6) or [])
                    edit_list[:] = []
                solved = await _anchor_pass(live, anchor_specs, float(margin), ref_size)
                if solved.get("ok") and not want:
                    want = [solved["view"]]
            if not want:
                az = float(at.get("az") or 0) + float((orbit or [0, 0])[0] or 0)
                el = float(at.get("el") or 0) + float((orbit or [0, 0, 0])[1] or 0) \
                    if len(orbit or []) > 1 else float(at.get("el") or 0)
                z = float(zoom or at.get("zoom") or 1) or 1
                spec = "az=%g,el=%g" % (round(az % 360, 1), round(max(-89, min(89, el)), 1))
                if abs(z - 1) > 0.001:
                    spec += ",zoom=%g" % z
                want = [spec]

            shots = []
            if edit_list:
                applied.extend(await live.ask("__forge.apply(%s)" % json.dumps(edit_list),
                                              depth=6) or [])
            if turn_steps:
                first = await live.raw("__forge.view(%s,%s)"
                                       % (json.dumps(want[0]), float(margin)))
                shots.append(("0deg", first))
                for i in range(1, turn_steps):
                    deg = round(360.0 * i / turn_steps)
                    await live.raw("__forge.turn(%d)" % deg)
                    shots.append(("%ddeg" % deg, await live.raw("__forge.shot()")))
                await live.raw("__forge.turn(0)")
            else:
                for v in want:
                    shots.append((v, await live.raw("__forge.view(%s,%s)"
                                                    % (json.dumps(v), float(margin)))))
            # THE LIGHT SWEEP. A matte surface and a glossy one are the same picture under
            # one fixed lamp, which is why three attempts at gloss on this bench changed nothing
            # measurable. Three frames with the key light moved show a finish; then it goes home.
            if sweep_n:
                for i in range(sweep_n):
                    deg = -50.0 + 100.0 * i / max(1, sweep_n - 1) if sweep_n > 1 else 0.0
                    await live.ask("__forge.keyAt(%g)" % deg, depth=2)
                    shots.append(("key %+d\u00b0" % round(deg), await live.raw("__forge.shot()")))
                await live.ask("__forge.keyAt(null)", depth=2)
            # MEASURED THROUGH THE VIEW IT IS LABELLED WITH. After several views the camera stands
            # at the last one, and `where` reads the camera that is set - so the numbers labelled
            # with the first view described the last. The camera goes back where it was after.
            refit = len(want) > 1 and not turn_steps
            if refit:
                await live.raw("__forge.view(%s,%s)" % (json.dumps(want[0]), float(margin)))
            placement = {"view": want[0],
                         "parts": await live.ask("__forge.where()", depth=6) or []}
            if refit:
                await live.raw("__forge.view(%s,%s)" % (json.dumps(want[-1]), float(margin)))
            for fname in focus_list:
                shots.append(("focus: %s @ %s" % (fname, want[0]),
                              await live.raw("__forge.view(%s,%s,0,%s)"
                                             % (json.dumps(want[0]), float(margin),
                                                json.dumps(fname)))))
            for pname in pass_list:
                if await live.ask("__forge.setPass(%s)" % json.dumps(pname)):
                    shots.append((pname, await live.raw("__forge.shot()")))
            await live.raw("__forge.clearPass()")

            score_shot = ""
            if score_ref:
                if await live.ask("__forge.setPass('silhouette')"):
                    score_shot = await live.raw("__forge.view(%s,%s)"
                                                % (json.dumps(want[0]), float(margin)))
                await live.raw("__forge.clearPass()")
                # Put the camera back where the pictures were taken from, so what the window
                # mirrors is what the caller was shown.
                await live.raw("__forge.view(%s,%s)" % (json.dumps(want[0]), float(margin)))
            detail_got = None
            if det_mode:
                detail_got = await _detail_pass(live, det_explicit, det_mode, tags, det_ref,
                                                want[0], float(margin), detail_views)
            return {"shots": shots, "stats": stats, "placement": placement,
                    "score_shot": score_shot, "detail": detail_got, "look": look_got,
                    "reverted": reverted, "sized": sized, "solved": solved,
                    "health": await live.ask("__forge.health()", depth=4) or [],
                    "holes": (await live.ask("__forge.holes()", depth=6) or {}) if _checks_on() else {},
                    "symmetry": (await live.ask("__forge.symmetry()", depth=5) or {}) if _checks_on() else {},
                    "gaps": (await live.ask("__forge.gaps()", depth=5) or {}) if _checks_on() else {},
                    "at": await live.ask("__forge.at()", depth=4) or {}}
        finally:
            await ws.close()

    try:
        got = _run(go)
    except Exception as ex:
        return {"ok": False, "error": str(ex).strip()[:1200]}
    if got.get("gone"):
        return {"ok": False, "error": "%s. Build it again with /api/live/forge." % got["gone"]}

    stats = got.get("stats") or {}
    place = got.get("placement") or {}
    stats["placement"] = dict(place)
    res: dict = {"ok": True, "stats": stats, "at": got.get("at") or {}, "looked": True}
    if got.get("reverted") is not None:
        n = int(got.get("reverted") or 0)
        res["reverted"] = n
        res.setdefault("notes", []).append(
            "put %d node%s back to what the builder made" % (n, "" if n == 1 else "s")
            if n else "no node had been edited, so nothing needed putting back")
    if isinstance(got.get("sized"), dict) and got["sized"].get("changed"):
        res["size"] = [got["sized"].get("width"), got["sized"].get("height")]
    if got.get("look"):
        # What the PAGE says the rig is, not what was asked for. A look that changes the light
        # and cannot confirm it costs a second call to find out.
        res["look"] = got["look"]

    # `frames_list`, not `frames`: the flag that asks for one PNG per view took that name.
    frames_list, labels = [], []
    for name, data in (got.get("shots") or []):
        im = _decode_shot(data)
        if im is None:
            res.setdefault("notes", []).append("view %s: %s" % (name, str(data)[:160]))
            continue
        frames_list.append(im)
        labels.append(name)
    if not frames_list:
        return {"ok": False, "error": "nothing was drawn. Is the scene still there?"}

    has_ref = False
    rf, why = (None, "") if _ref_off(ref) else _ref_frame(project, ref, frames_list[0].size)
    if rf is not None:
        frames_list.insert(0, rf)
        labels.insert(0, "REFERENCE (the target)")
        has_ref = True
    elif why:
        res.setdefault("notes", []).append(why)

    res["findings"] = _findings(stats, has_ref, focus_list, ref_off=_ref_off(ref))
    res["findings"][0:0] = view_notes
    if applied:
        # WHAT EACH EDIT MOVED. A target that matched nothing, or matched two copies of the same
        # asset, changed the picture in a way the agent could not see from the answer.
        res["edits"] = applied
        miss = [str(a.get("target")) for a in applied if isinstance(a, dict) and not a.get("found")]
        many = [a for a in applied if isinstance(a, dict) and int(a.get("found") or 0) > 1]
        if miss:
            res["findings"].insert(0, "EDITS: %s matched nothing - a target is a node's name, exact "
                                      "first, then part of a name." % ", ".join(repr(m) for m in miss[:5]))
        if many:
            res["findings"].insert(0, "EDITS: " + "; ".join(
                "%r moved %d nodes (%s)" % (a.get("target"), int(a.get("found") or 0),
                                            ", ".join(str(n) for n in (a.get("names") or [])[:4]))
                for a in many[:3]))
        res.setdefault("notes", []).append(
            "edits add up across calls (a move of 2 sent twice is 4); edits:\"reset\" puts every "
            "one back")
    if place:
        res["placement"] = place
        drift = _drift_finding(place, (_last_run(project, label).get("placement") or {}))
        if drift:
            res["drift"] = drift
            res["findings"].insert(0, drift)
        pf = _placement_finding(place)
        if pf:
            res["findings"].append(pf)
    # ONLY FROM THE REFERENCE'S OWN ANGLE. A look is usually round the side; a front reference
    # scored against a side silhouette is a number that means nothing, and one path through it
    # was an HTTP 500.
    # NEAR IT IS ENOUGH. Only the exact string once scored, so an agent choosing between el=18
    # and el=26 for a front reference got a score for one and silence for the other.
    view0 = labels[1] if has_ref and len(labels) > 1 else labels[0]
    solved = got.get("solved") or {}
    rv = _ref_view(project) if score_ref else ""
    # A view the anchors solved IS the reference's angle, whatever an older build scored from.
    if (score_ref and rv and view0 != rv and not _near_view(view0, rv)
            and not (solved.get("ok") and view0 == solved.get("view"))):
        res.setdefault("notes", []).append(
            "no score: %s is more than %d degrees from the angle the reference is scored from (%s)"
            % (view0, int(NEAR_REF_DEG), rv))
        score_ref = ""
    if score_ref:
        sil = _decode_shot(got.get("score_shot") or "")
        cmp = _score_against(score_ref,
                             sil if sil is not None else frames_list[1 if has_ref else 0],
                             view0, drawn=sil is not None,
                             ref_mask=reference(project).get("mask") or "")
        if cmp:
            res["score"] = cmp
            for line in reversed(_score_finding(cmp, (_last_run(project, label).get("score") or {}))):
                res["findings"].insert(0, line)
            band = _band_finding(cmp)
            if band:
                res["findings"].append(band)
            if solved.get("ok") and view0 == solved.get("view"):
                solved["overlap"] = cmp.get("overlap")
        # THE DARKS, not only the outline: the lit render at the reference's own angle.
        con = {} if not _checks_on() else _contrast(frames_list[1 if has_ref else 0], score_ref,
                        reference(project).get("mask") or "")
        if con:
            res["contrast"] = con
            cl = _contrast_finding(con)
            if cl:
                res["findings"].append(cl)
    # THE TARGET, AS NUMBERS. Off unless asked: interesting only while matching a reference.
    if facts:
        rp = (reference(project).get("reference") or "")
        if rp:
            res["reference"] = ref_facts(rp, reference(project).get("mask") or "")
    res["findings"].extend(_health_finding(got.get("health")))
    res["findings"].extend(_hole_finding(got.get("holes")))
    _hole_summary(res, got.get("holes"))
    res["findings"].extend(_symmetry_finding(got.get("symmetry")))
    res["findings"].extend(_gap_finding(got.get("gaps")))
    _detail_out(project, got.get("detail"), str(stats.get("engine") or ""), label or "look", res)
    if anchor_specs:
        # THE SOLVE LEADS: it is what this look was asked for.
        if solved.get("ok"):
            res["solved"] = _anchor_public(solved)
            res["snap"] = solved.get("snap") or []
            lines = _anchor_lines(solved)
        else:
            lines = [solved.get("error") or "ANCHORS: the camera could not be solved"]
            lines.extend(solved.get("warnings") or [])
        if anchor_bad:
            lines.append("ANCHORS: ignored %s." % "; ".join(anchor_bad[:4]))
        res["findings"][0:0] = lines

    from . import review as _review
    want_q = str(quality or _engine_pref("forge_quality")
                 or settings.get("cc_review_quality") or "normal").lower()
    budget = _review.QUALITY.get(want_q, _review.QUALITY["normal"])[0]
    sub = "looked again · %s tris · %s materials" % (stats.get("triangles", "?"),
                                                     stats.get("materials", "?"))
    sheet = _review._sheet(frames_list, labels, label or "look", sub, budget)
    out = _LIVE_DIR / _slug(project) / ("forge-%d.png" % int(time.time() * 1000))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    res["sheet"] = str(out)
    res["views"] = labels
    # ONE PNG PER VIEW, WHEN ASKED. Every consumer of a sheet has had to cut the panels back out
    # of it - which cost `fronts.py` four rounds of crop bugs and one agent a whole ruined sheet -
    # and the frames exist here, uncomposed, for nothing.
    if frames:
        made = []
        for i, (im, name) in enumerate(zip(frames_list, labels)):
            fp = out.with_name(out.stem + "-%d-%s.png" % (i, _slug(str(name))[:24]))
            try:
                im.save(fp)
                made.append(str(fp))
            except OSError:
                pass
        res["frames"] = made

    # Recorded like any other generation, with the code the session is holding — so a look is in
    # the strip beside the build it looked at, and can be reopened in the editor.
    try:
        from . import engine as _engine
        gid = _engine.record(project, label or "look", session_code(project), stats.get("engine", ""),
                             stats, labels, str(out), True, "", "", tags=tags, category=category,
                             extra={"looked": True, "at": res.get("at") or {},
                                    "detail": res.get("detail") or [],
                                    "detail_sheet": res.get("detail_sheet") or "",
                                    "score": res.get("score") or {},
                                    "placement": res.get("placement") or {}})
        if gid:
            res["generation"] = gid
    except Exception:
        pass
    # A look changes no geometry, so only the camera moves on the bench.
    _bench_put(project, "", False, res.get("at") or {}, stats, label)
    _done(project, "looked", ", ".join(labels[1:] if has_ref else labels)[:120])
    return res


# ---------------------------------------------------------------------------
# Aiming the camera at the reference
#
# The worst thing the forge did to an agent was hand it `3q` and `side` to judge against a
# photograph taken from neither. Every proportion it then read was read across two different
# angles, and one agent wrote in its own notes: "the front view is invention."
#
# A camera angle is a search, and a search is work for a machine, not for a language model. A
# silhouette is cheap, the reference is a picture we already hold, and the overlap between two
# silhouettes is one number. So: sweep the sphere, score every angle, hand back the best one AS A
# NUMBER. No image is returned, and none is charged for.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# What the forge is doing, while it is doing it
#
# A generation says what was MADE. This says what is being made, and it exists because the cheap
# half of the loop draws no picture: an agent can measure, move a part and measure again for a
# minute without a single sheet, and from outside that is indistinguishable from an agent that has
# stopped. One dict, written by whichever thread is running the call, read by /api/engine/state.
# ---------------------------------------------------------------------------

_ACTIVITY: dict = {}
_ACTIVITY_KEEP = 20.0        # seconds a finished action stays on screen before it fades
# A row still "running" after this long is not running. The Engine window showed
# "building · probe2 · 3q 1633s" — a forge call that returned early and never said so.
_ACTIVITY_STALE = 600.0
# The row THIS thread opened, so `_settles` closes that one and not a newer call's row.
_ACT_TL = threading.local()


def _doing(project: str, phase: str, detail: str = "", label: str = "") -> None:
    row = {"project": str(project), "project_name": Path(project).name,
           "phase": phase, "detail": detail[:200], "label": label[:80],
           "since": time.time()}
    _ACTIVITY[_slug(project)] = row
    _ACT_TL.row = row


def _done(project: str, phase: str = "done", detail: str = "") -> None:
    row = _ACTIVITY.get(_slug(project))
    if row:
        row.update({"phase": phase, "detail": detail[:200], "ended": time.time()})


def activity() -> list:
    """Everything the forge is doing or has just finished, newest first."""
    now = time.time()
    out = []
    for k, row in list(_ACTIVITY.items()):
        if not row.get("ended") and now - float(row.get("since") or now) > _ACTIVITY_STALE:
            # Whatever opened this row never closed it. Say so once, then let it fade.
            row.update({"phase": "no answer",
                        "detail": "no reply after %d minutes - the call that started this never "
                                  "finished" % int((now - float(row.get("since") or now)) // 60),
                        "ended": now})
        if row.get("ended") and now - row["ended"] > _ACTIVITY_KEEP:
            _ACTIVITY.pop(k, None)
            continue
        r = dict(row)
        r["running"] = not row.get("ended")
        r["seconds"] = round(now - float(row.get("since") or now), 1)
        out.append(r)
    out.sort(key=lambda r: r.get("since") or 0, reverse=True)
    return out[:6]


def _mask_of_render(im):
    """The subject, as True pixels. The silhouette pass draws it black on near-white."""
    import numpy as np
    return np.asarray(im.convert("L"), dtype=np.int16) < 128


_REF_MASKS: dict = {}


def _mask_of_reference(path: str, mask_path: str = ""):
    """The subject in the reference picture: the mask the caller supplied, or read off the file.

    Cached on the file's own modification time. The reference does not change between calls and
    the threshold ladder in `_mask_of_photo` tidies up to five times, so every score used to pay
    for the same segmentation again.
    """
    from PIL import Image
    key = None
    try:
        key = (path, os.path.getmtime(path), mask_path,
               os.path.getmtime(mask_path) if mask_path else 0)
        hit = _REF_MASKS.get(key)
        if hit is not None:
            return hit
    except OSError:
        key = None
    ref = Image.open(path).convert("RGB")
    out = None
    if mask_path:
        try:
            import numpy as np
            m = np.asarray(Image.open(mask_path).convert("L")) > 127
            if m.shape[0] == ref.height and m.shape[1] == ref.width and m.any():
                out = m
        except Exception:
            out = None                 # a mask that does not fit is not a mask; fall through
    if out is None:
        out = _mask_of_photo(ref)
    if key is not None:
        if len(_REF_MASKS) > 8:
            _REF_MASKS.clear()
        _REF_MASKS[key] = out
    return out


def _mask_of_photo(im, edges: bool = True):
    """The subject in a LIT, COLOURED picture, by distance from its own background colour.

    Not the same job as `_mask_of_render`, and the difference cost a real number. That one reads
    the SILHOUETTE pass — every mesh painted black on near-white — so it is a `< 128` threshold.
    Point it at an ordinary lit render on the forge's dark backdrop and it selects the BACKDROP
    (which is dark) and rejects the subject (which is bright): the mask comes back as the whole
    frame, every height band exactly the panel width, and the score is nonsense that looks like a
    score. An agent caught it by cross-checking a render against `aim` — 0.62 against 0.91 on the
    same chest.

    The background is read from the four corners rather than assumed: a product shot on white and
    a game render on near-black are both ordinary, and a fixed threshold would keep one and drop
    the other entirely."""
    import numpy as np
    a = np.asarray(im.convert("RGB"), dtype=np.int16)
    ring = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    bg = np.median(ring, axis=0)
    d = np.abs(a - bg).sum(axis=2)
    # "As different as the background ever gets from itself", read off the border rather than
    # assumed. A studio backdrop is never one flat colour — it has a vignette, a gradient, a soft
    # shadow — so a threshold under this is measuring the plate.
    #
    # The 90th percentile, not the 99.5th, and that is not a detail. A crop cut out of a SHEET
    # clips the panels beside it, so a border pixel in a hundred is not plate at all: on the real
    # turnaround crop p50 was 2, p75 3, p90 5 and p99 36. The high percentile read the neighbour
    # and set the floor at 52, which locked the ladder below to its top rung and lost the legs.
    noise = float(np.percentile(np.abs(ring - bg).sum(axis=1), 90.0)) * 3.0
    # DISTANCE ALONE LOSES A SUBJECT THAT IS THE BACKGROUND COLOUR. The roblox-boy reference is a
    # near-black hoodie and near-black jeans on a near-black plate. Cloth still has seams, folds
    # and a lit rim where the plate has nothing, so the gradient finds what the colour distance
    # cannot. It is the same map at every threshold, so it is built once.
    edge_m = None
    if edges:
        g = a.mean(axis=2)
        gx = np.zeros_like(g)
        gy = np.zeros_like(g)
        gx[:, 1:-1] = g[:, 2:] - g[:, :-2]
        gy[1:-1, :] = g[2:, :] - g[:-2, :]
        mag = np.hypot(gx, gy)
        edge_m = mag > max(5.0, float(np.percentile(mag, 99.0)) * 0.16)
    # THE THRESHOLD IS A LADDER, NOT A NUMBER, and this is the fix for the fault above. It used
    # to be `max(60, noise)`. On a plate uniform to one level `noise` is about 4, so the 60
    # decided — and charcoal denim on a near-black plate is 44 away from it. Measured on the real
    # turnaround crop: the mask kept 542 px of the 13,896 the shins occupy, and the score read
    # 0.38 where a hand-made mask read 0.86. Nothing in the answer said the reference was wrong.
    #
    # So walk DOWN from the old constant and keep the last step that still describes a subject.
    # A step that is really the plate does not add a limb, it adds the whole picture: it either
    # covers most of the frame or it more than doubles what the step above it found. Either of
    # those ends the walk and the step before it is the answer.
    steps = sorted({t for t in (max(60.0, noise), 40.0, 26.0, 18.0, 12.0, 8.0)
                    if t >= max(8.0, noise)}, reverse=True)
    best = None
    for thr in steps:
        m = (d > thr) if edge_m is None else ((d > thr) | edge_m)
        m = _tidy_mask(m, edges)
        if m is None or not m.any():
            continue
        cover = float(m.mean())
        if best is not None:
            was = float(best.mean())
            if cover >= 0.80 or (was >= 0.05 and cover > was * 2.5):
                break
        best = m
    return best if best is not None else (d > (steps[0] if steps else 60.0))


def _tidy_mask(m, edges: bool = True):
    """THE SUBJECT IS THE BIGGEST THING, AND NOTHING ELSE IS.

    A reference is a picture someone pasted: it has a watermark, a cursor, a frame, a stray red
    pixel in a corner. Every one of those survives the threshold, and a bounding box drawn around
    all of them together is a box around the whole picture — so the silhouette score compared the
    model against the page. Closed first, so an outline made of edges becomes a body; filled, so
    the body is solid.
    """
    import numpy as np
    try:
        from scipy import ndimage
    except Exception:
        # No scipy: the mask is honest, just not tidied. Better than pretending it was.
        return m
    try:
        if edges:
            m = ndimage.binary_closing(m, np.ones((5, 5), bool))
            m = ndimage.binary_fill_holes(m)
        lab, n = ndimage.label(m)
        if n > 1:
            sizes = ndimage.sum(m, lab, range(1, n + 1))
            m = lab == (int(np.argmax(sizes)) + 1)
        m = ndimage.binary_fill_holes(m)
        if edges:
            # A central-difference gradient marks one pixel OUTSIDE the subject, so the union is a
            # pixel fat all round: measured 174 px across the reference's hips where the agent
            # measured 172 by hand. Eroded by one, it reads 172.
            m = ndimage.binary_erosion(m, np.ones((3, 3), bool))
    except Exception:
        pass
    return m


def _crop_mask(m):
    import numpy as np
    ys, xs = np.where(m)
    if not len(ys):
        return None
    return m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def _compare_masks(model, target, bands: int = 6):
    """How alike two silhouettes are, and WHERE they differ.

    Normalised by HEIGHT, never by area or by width. Both pictures show the same creature, and
    height is the one measurement a brief fixes ("about 2.4 units tall"). Normalising by width
    would hide a tail half again too long, which is exactly the fault worth catching."""
    import numpy as np
    from PIL import Image
    A, B = _crop_mask(model), _crop_mask(target)
    if A is None or B is None:
        return None
    th = int(B.shape[0])
    tw = max(1, int(round(A.shape[1] * th / max(1, A.shape[0]))))
    A2 = np.asarray(Image.fromarray((A * 255).astype("uint8")).resize((tw, th),
                                                                      Image.NEAREST)) > 127
    W = max(int(B.shape[1]), tw) + 2
    ca = np.zeros((th, W), bool)
    cb = np.zeros((th, W), bool)
    ax, bx = (W - tw) // 2, (W - int(B.shape[1])) // 2
    ca[:, ax:ax + tw] = A2
    cb[:, bx:bx + int(B.shape[1])] = B
    union = int((ca | cb).sum())
    rows = []
    for i in range(bands):
        y0, y1 = th * i // bands, th * (i + 1) // bands
        mw = int(ca[y0:y1].any(axis=0).sum())
        rw = int(cb[y0:y1].any(axis=0).sum())
        rows.append({"from": round(i / bands, 2), "to": round((i + 1) / bands, 2),
                     "model_px": mw, "ref_px": rw,
                     "ratio": round(mw / rw, 2) if rw else None})
    return {"overlap": round(int((ca & cb).sum()) / float(union or 1), 3),
            "width_ratio": round(tw / max(1, int(B.shape[1])), 2), "bands": rows}


# How close two overlaps have to be before a silhouette is admitting it cannot tell them apart.
# Measured on the chest: the front and the back scored 0.889 and 0.887.
_MIRROR = 0.02


def _az_of(view: str):
    """The azimuth a view spec asks for, or None if it is a named preset."""
    for part in str(view or "").replace(";", ",").split(","):
        bits = part.split("=")
        if len(bits) == 2 and bits[0].strip().lower() in ("az", "azimuth"):
            try:
                return float(bits[1]) % 360.0
            except ValueError:
                return None
    return None


def _mirror_finding(scored: list) -> str:
    """A warning when the best angle has a twin about half a turn away, scoring the same.

    A silhouette has no front and no back. A chest photographed from az=150 and from az=330 casts
    almost the same outline, so the sweep ranks them within noise of each other and hands back
    whichever won by a thousandth. Picking the wrong one is not a small error: it photographs the
    back of the subject and every later judgement is made against the wrong face. It has happened
    — an agent shipped a chest showing the open end of its own lid.

    The overlap cannot break the tie. `where()` can, because it says which named part landed on
    which side of the frame.
    """
    if not scored:
        return ""
    best = scored[0]
    a0 = _az_of(best.get("view", ""))
    if a0 is None:
        return ""
    for c in scored[1:]:
        a1 = _az_of(c.get("view", ""))
        if a1 is None:
            continue
        gap = abs(((a1 - a0 + 180.0) % 360.0) - 180.0)
        if gap < 120.0:
            continue
        if abs(float(best.get("overlap") or 0) - float(c.get("overlap") or 0)) > _MIRROR:
            continue
        return ("MIRROR: %s scores %.3f and %s scores %.3f - within noise of each other, so the "
                "silhouette cannot tell the front of this subject from the back. Pick between "
                "them with the placement numbers, not this score: render one, read "
                "`placement.parts`, and check that a part you know belongs on the front is on "
                "the side of the frame the reference puts it."
                % (best["view"], float(best.get("overlap") or 0), c["view"],
                   float(c.get("overlap") or 0)))
    return ""


def _decode_shot(data: str):
    """One `data:image/...` capture as a PIL image, or None if it is not one."""
    if not isinstance(data, str) or not data.startswith("data:image"):
        return None
    try:
        from PIL import Image
        import io as _io
        return Image.open(_io.BytesIO(base64.b64decode(data.split(",", 1)[1]))).convert("RGB")
    except Exception:
        return None


def _score_against(ref_path: str, frame, view: str, drawn: bool = False,
                   ref_mask: str = "") -> dict:
    """How much of the reference's silhouette this render covers, on EVERY shot.

    This used to happen only inside `aim`, the 36-angle sweep. So an agent that passed a reference
    to `forge` got the picture drawn into its sheet, got `overlap: null`, and had nothing to judge
    by except its own eyes — which is the expensive way, and the way that let a chest get worse
    between two shots without anyone noticing. The masks and the render are already in hand here;
    the compare is milliseconds.

    `drawn` says which KIND of frame this is, and getting it wrong is not a small error.
    A silhouette pass is black on near-white and is read by threshold. A lit colour render has to
    be read by distance from its own backdrop. Pointed at the wrong one, the threshold reader
    selects the dark backdrop and rejects the bright subject: measured at 0.554 where the truth
    was 0.899.
    """
    try:
        target = _mask_of_reference(ref_path, ref_mask)
        cmp = _compare_masks(_mask_of_render(frame) if drawn else _mask_of_photo(frame), target)
    except Exception:
        return {}
    if not cmp:
        # An empty silhouette on one side: nothing drawn at that angle, or a reference the mask
        # reader could not separate. `cmp["view"] = view` on None was an HTTP 500 from /look —
        # found by the brainrot session with a reference set and views=["left"].
        return {}
    cmp["view"] = view
    cmp["reference"] = ref_path
    cmp["from"] = "silhouette" if drawn else "lit render"
    if ref_mask:
        cmp["mask"] = Path(ref_mask).name
    return cmp


def _last_run(project: str, label: str) -> dict:
    """The PREVIOUS run under this label — its score and where its parts were.

    Kept in the generation records, which are already written beside every sheet. Nothing new is
    stored; this only reads what the archive holds.
    """
    if not label:
        return {}
    key = str(label).strip().lower()
    # Straight from the records on disk, not through `history()`. The history list is shaped for
    # the Engine window and carries a fixed set of fields; the score is not one of them, so
    # reading it there found the run and none of its numbers.
    folder = _LIVE_DIR / _slug(project)
    try:
        files = sorted(folder.glob("forge-*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    except OSError:
        return {}
    for f in files[:60]:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(rec.get("label") or "").strip().lower() != key:
            continue
        if rec.get("score") or rec.get("placement"):
            return rec
    return {}


def _score_finding(cmp: dict, before: dict) -> list:
    """The two lines an agent should read before it opens the picture."""
    out: list = []
    if not cmp or cmp.get("overlap") is None:
        return out
    now = float(cmp["overlap"])
    line = ("MATCH: your silhouette covers %.2f of the reference's from %s, and at the same "
            "height it is %.2f as wide." % (now, cmp.get("view") or "this angle",
                                            float(cmp.get("width_ratio") or 0)))
    if now < 0.55:
        line += (" Under 0.55 the shapes are not the same shape yet — check the proportions "
                 "before any detail work.")
    if cmp.get("mask"):
        # A mask passed once is kept for every later score. Useful, and it was invisible: the
        # target's top band moved from 151 to 136 pixels with nothing in the answer to say why.
        line += (" Scored through your ref_mask %s (kept for this project; ref_mask:\"-\" "
                 "forgets it)." % cmp["mask"])
    out.append(line)
    was = before.get("overlap") if isinstance(before, dict) else None
    if was is not None:
        d = now - float(was)
        if d <= -0.02:
            # The OUTLINE got worse. Detail that lives inside the outline is caught by the drift
            # line instead — see `_drift_finding`, which is the one that sees a moved inset.
            out.append("WORSE THAN YOUR LAST SHOT: %.2f, down from %.2f. Something you changed "
                       "cost you the outline — proportions, or a part that reaches the edge."
                       % (now, float(was)))
        elif d >= 0.02:
            out.append("Better than your last shot: %.2f, up from %.2f." % (now, float(was)))
        else:
            out.append("The same as your last shot, within noise: %.2f against %.2f."
                       % (now, float(was)))
    return out


# How far a part may move between two shots before it is worth a line. Two hundredths of the
# frame is about six pixels on a 320px panel: below that it is a rounding difference in the
# camera framing, above it somebody moved something.
_DRIFT = 0.02


def _drift_finding(now: dict, before: dict) -> str:
    """What moved, what went missing, and what appeared — since the last shot under this label.

    THE SILHOUETTE CANNOT DO THIS JOB. An outline is an outline: slide a diamond inset across the
    front of a chest and the overlap score does not move, because nothing about the edge changed.
    Measured on a real regression here, the score went UP by 0.008 while the diamond travelled a
    tenth of the frame to the left. Positions catch what the outline is blind to.
    """
    a = {q.get("name"): q for q in (before.get("parts") or []) if isinstance(q, dict) and q.get("at")}
    b = {q.get("name"): q for q in (now.get("parts") or []) if isinstance(q, dict) and q.get("at")}
    if not a or not b:
        return ""
    if (before.get("view") or "") != (now.get("view") or ""):
        return ""                                    # two different cameras: not comparable
    moved = []
    for name, q in b.items():
        was = a.get(name)
        if not was:
            continue
        dx = float(q["at"][0]) - float(was["at"][0])
        dy = float(q["at"][1]) - float(was["at"][1])
        if max(abs(dx), abs(dy)) >= _DRIFT:
            where = []
            if abs(dx) >= _DRIFT:
                where.append("%s %.2f" % ("right" if dx > 0 else "left", abs(dx)))
            if abs(dy) >= _DRIFT:
                where.append("%s %.2f" % ("down" if dy > 0 else "up", abs(dy)))
            moved.append((max(abs(dx), abs(dy)), "%s %s (%.2f,%.2f -> %.2f,%.2f)"
                          % (name, " and ".join(where), was["at"][0], was["at"][1],
                             q["at"][0], q["at"][1])))
    gone = [n for n in a if n not in b]
    new_parts = [n for n in b if n not in a]
    if not (moved or gone or new_parts):
        return ""
    bits = []
    if moved:
        moved.sort(reverse=True)
        bits.append("moved: " + "; ".join(t for _, t in moved[:6]))
    if gone:
        bits.append("GONE: " + ", ".join(sorted(gone)[:8]))
    if new_parts:
        bits.append("new: " + ", ".join(sorted(new_parts)[:8]))
    return ("SINCE YOUR LAST SHOT — " + " | ".join(bits)
            + ". If you did not mean to change these, that is what your edit cost you.")


def _placement_finding(placement: dict) -> str:
    """Where the biggest parts sit in the frame, in the reference panel's own coordinates."""
    parts = [q for q in (placement.get("parts") or []) if isinstance(q, dict)]
    if not parts:
        return ""
    big = sorted(parts, key=lambda q: -(q.get("tris") or 0))[:6]

    def spot(q):
        # WHERE IT SHOWS, when the page measured that: the centre of the part's visible pixels
        # (`px_at`, from a flat-colour id render). A projected box is not where a part is - the
        # forge goblin's helmet box "starts well above the helmet".
        p = q.get("px_at")
        if isinstance(p, list) and len(p) == 2:
            return p
        return q["at"] if isinstance(q.get("at"), list) and len(q["at"]) == 2 else None

    bits = ", ".join("%s at (%.2f, %.2f)" % (q.get("name") or "?", spot(q)[0], spot(q)[1])
                     for q in big if spot(q) is not None)
    if not bits:
        return ""
    pix = any(isinstance(q.get("px_at"), list) for q in big)
    return ("PLACED, in the %s view: %s. The reference panel is ruled in the same 0..1 — x from "
            "the left edge, y from the top — so read the target off it and subtract. The full "
            "list is in `placement.parts`%s."
            % (placement.get("view") or "first", bits,
               " - these are the centres of each part's VISIBLE pixels (`px_at`, `px_box`); "
               "`at` and `box` stay the projected bounding box" if pix else ""))


def _band_finding(cmp: dict) -> str:
    """The bands that miss the reference by most, named by where they are."""
    rows = [r for r in (cmp.get("bands") or []) if r.get("ratio")]
    if not rows:
        return ""
    rows.sort(key=lambda r: abs(1.0 - r["ratio"]), reverse=True)
    bits = []
    for r in rows[:2]:
        if abs(1.0 - r["ratio"]) < 0.12:
            continue
        bits.append("%d-%d%% down from the top is %d%% %s than the reference"
                    % (r["from"] * 100, r["to"] * 100, abs(round((r["ratio"] - 1) * 100)),
                       "wider" if r["ratio"] > 1 else "narrower"))
    return "; ".join(bits)


# WHICH WAY AN ANGLE IS FACING, and it has to be true. The convention is written down in the
# forge, and an agent had to go and read `live_forge.py` to find it before it dared use the number
# `aim` gave it. Until 2026-09-24 this line said "az=90 at its right side" and "az=270 at its
# left" - backwards under glTF, whose subject faces +Z with its OWN left at +X, which is where
# az=90 stands. The goblin A/B's forge builder read it that way round: `az=22` put its camera on
# the goblin's left while the sentence said right.
FACING = ("FACING: az=0 looks at the subject's front (+Z), az=90 at its LEFT side (+X), "
          "az=180 at its back, az=270 at its right side (-X) - glTF's convention: the subject "
          "faces +Z and its own left is +X. So the preset `side` (az=90) shows its left side, and "
          "`left` (az=270, on your left as you face it) shows its right side. el lifts the camera "
          "above the horizon.")


# ---------------------------------------------------------------------------
# The camera, solved from points the picture shows
#
# A silhouette has no front and no back: the goblin A/B's forge builder got its top five `aim`
# angles within 0.012 of each other and could not choose the side the picture was taken from. The
# Blender builder solved its camera from three points it could see (helmet apex, nose, buckle) and
# then moved ear tips, fists, shield and sword tip onto the picture's own pixels - "High for the
# picture's angle". This is that, for the bench.
#
# THE MODEL IS THE BENCH'S OWN CAMERA. For `az=A,el=E,zoom=Z` the forge stands at the subject's
# bounding centre plus the view direction times radius/tan(19 deg) * margin/Z, looks at the centre
# with +Y up, and projects with a 38 degree vertical field of view (F.view - and a written zoom is
# never refitted, so the camera solved is the camera rendered). The picture was taken with some
# other lens and framed its own way, so the bench's image is carried onto the reference's pixels by
# a SIMILARITY - one scale, one offset, no turn - solved in closed form for every candidate camera.
# What that leaves over is the error, in the reference's own pixels.
# ---------------------------------------------------------------------------
_FOV = 38.0                     # the bench's vertical field of view (PerspectiveCamera(38, ...))
_ANCHOR_WHERE = ("top", "bottom", "left", "right", "front", "back", "centre", "center",
                 "+x", "-x", "+y", "-y", "+z", "-z")
_ANCHOR_MIN = 3
# Three anchors fix the angle and the framing and leave one equation over; perspective needs a
# fourth. Below this many the zoom is held at the bench's own framing distance, zoom=1.
_ANCHOR_ZOOM_FROM = 4
# Degrees the answer moves per pixel of anchor error before the solve is called poorly
# conditioned, and how thin the anchors may be across their own line before they are "in a line".
_ANCHOR_SLOPPY = 3.0
_ANCHOR_THIN = 0.08


def _anchor_specs(anchors) -> tuple:
    """(clean anchors, problems) out of what the caller sent.

    An anchor is {"at": [x, y, z] | {"part": name, "where": top|bottom|left|right|front|back|
    centre}, "px": [x, y]} with px in the reference's own pixels. "solve": false leaves it out of
    the camera and only snaps it - the Blender builder's way: solve from trusted points, then move
    the ear tips and the sword tip onto theirs."""
    out, bad = [], []
    for i, a in enumerate(anchors or []):
        n = i + 1
        if not isinstance(a, dict):
            bad.append("anchor %d is not an object" % n)
            continue
        try:
            px = [float(a["px"][0]), float(a["px"][1])]
        except (KeyError, TypeError, ValueError, IndexError):
            bad.append("anchor %d has no \"px\": [x, y] in the reference's own pixels" % n)
            continue
        at = a.get("at")
        if isinstance(at, (list, tuple)):
            try:
                at = [float(at[0]), float(at[1]), float(at[2])]
            except (TypeError, ValueError, IndexError):
                bad.append("anchor %d: \"at\" is [x, y, z] or {\"part\": ..., \"where\": ...}" % n)
                continue
            label = "(%.3g, %.3g, %.3g)" % tuple(at)
        elif isinstance(at, dict) and str(at.get("part") or "").strip():
            where = str(at.get("where") or "centre").strip().lower() or "centre"
            at = {"part": str(at["part"]).strip(), "where": where}
            label = "%s %s" % (at["part"], where)
        else:
            bad.append("anchor %d: \"at\" is [x, y, z] or {\"part\": ..., \"where\": ...}" % n)
            continue
        out.append({"n": n, "at": at, "px": px, "solve": a.get("solve", True) is not False,
                    "label": str(a.get("name") or label)})
    return out, bad


def _basis(az, el):
    """The bench camera's unit axes for these angles (scalars or arrays): d from the subject toward
    the camera, x to the right of the picture, y up it. three's lookAt, with +Y up."""
    import numpy as np
    a = np.radians(np.asarray(az, dtype=float))
    e = np.radians(np.clip(np.asarray(el, dtype=float), -89.0, 89.0))
    d = np.stack([np.sin(a) * np.cos(e), np.sin(e), np.cos(a) * np.cos(e)], -1)
    x = np.stack([d[..., 2], np.zeros_like(d[..., 0]), -d[..., 0]], -1)
    x = x / np.linalg.norm(x, axis=-1, keepdims=True)
    return d, x, np.cross(d, x)


def _cam_dist(radius, zoom, margin, ortho, fov=_FOV):
    """How far F.view stands the camera from the subject's centre for this zoom."""
    import numpy as np
    if ortho:
        return np.full(np.shape(zoom), radius * 4 + 0.001) if np.ndim(zoom) else radius * 4 + 0.001
    return radius / math.tan(math.radians(fov) / 2.0) * (margin / np.asarray(zoom, dtype=float))


def _cam_project(P, c, radius, az, el, zoom, margin, ortho, fov=_FOV):
    """World points (N, 3) -> the bench camera's normalised image coordinates.

    (u, v, depth, eye, (d, x, y)): u right and v up, x/-z and y/-z of the camera frame (x and y
    under ortho), depth negative in front of the camera."""
    import numpy as np
    d, x, y = _basis(az, el)
    eye = np.asarray(c, float) + d * float(_cam_dist(radius, zoom, margin, ortho, fov))
    rel = np.asarray(P, float) - eye
    xc, yc, zc = rel @ x, rel @ y, rel @ d
    if ortho:
        return xc, yc, zc, eye, (d, x, y)
    with np.errstate(divide="ignore", invalid="ignore"):
        return xc / -zc, yc / -zc, zc, eye, (d, x, y)


def _similarity(q, r):
    """(s, t) with s*q + t closest to r by least squares, s > 0; None when it cannot be."""
    import numpy as np
    qm, rm = q.mean(0), r.mean(0)
    Q, R = q - qm, r - rm
    den = float((Q * Q).sum())
    if den <= 1e-18:
        return None
    s = float((Q * R).sum()) / den
    if not s > 0:
        return None
    return s, rm - s * qm


def _anchor_cost(P, R, c, radius, az, el, zoom, margin, ortho, fov=_FOV):
    """Sum of squared pixel errors for G candidate cameras at once (arrays of length G)."""
    import numpy as np
    az, el, zoom = (np.atleast_1d(np.asarray(v, dtype=float)) for v in (az, el, zoom))
    d, x, y = _basis(az, el)
    dist = _cam_dist(radius, zoom, margin, ortho, fov)
    eye = np.asarray(c, float)[None, :] + d * np.asarray(dist, float).reshape(-1, 1)
    rel = P[None, :, :] - eye[:, None, :]
    xc = np.einsum("gnk,gk->gn", rel, x)
    yc = np.einsum("gnk,gk->gn", rel, y)
    zc = np.einsum("gnk,gk->gn", rel, d)
    with np.errstate(divide="ignore", invalid="ignore"):
        u, v = (xc, yc) if ortho else (xc / -zc, yc / -zc)
        q = np.stack([u, -v], -1)
        qm = q.mean(1, keepdims=True)
        rm = R.mean(0)
        Q, Rc = q - qm, R - rm
        den = (Q * Q).sum((1, 2))
        s = (Q * Rc[None]).sum((1, 2)) / np.where(den > 1e-18, den, np.nan)
        t = rm[None, :] - s[:, None] * qm[:, 0, :]
        e = s[:, None, None] * q + t[:, None, :] - R[None]
        cost = (e * e).sum((1, 2))
    bad = ~(s > 0) | ~np.isfinite(cost)
    if not ortho:
        bad |= (zc >= -1e-9).any(1)
    cost[bad] = np.inf
    return cost


def _nelder_mead(f, x0, step, iters: int = 600):
    """A small, deterministic Nelder-Mead: (best point, its value)."""
    import numpy as np
    x0 = np.asarray(x0, float)
    n = len(x0)
    pts = [x0] + [x0 + np.eye(n)[i] * step[i] for i in range(n)]
    vals = [f(p) for p in pts]
    for _ in range(iters):
        order = np.argsort(vals)
        pts, vals = [pts[i] for i in order], [vals[i] for i in order]
        if (np.isfinite(vals[-1]) and vals[-1] - vals[0] <= 1e-13 * (1.0 + abs(vals[0]))
                and max(float(np.abs(p - pts[0]).max()) for p in pts[1:]) < 1e-9):
            break
        c = np.mean(pts[:-1], 0)
        xr = c + (c - pts[-1])
        fr = f(xr)
        if fr < vals[0]:
            xe = c + 2.0 * (c - pts[-1])
            fe = f(xe)
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = c + 0.5 * (pts[-1] - c)
            fc = f(xc)
            if fc < vals[-1]:
                pts[-1], vals[-1] = xc, fc
            else:
                pts = [pts[0]] + [pts[0] + 0.5 * (p - pts[0]) for p in pts[1:]]
                vals = [vals[0]] + [f(p) for p in pts[1:]]
    i = int(np.argmin(vals))
    return pts[i], vals[i]


def _view_gap(a1, e1, a2, e2) -> float:
    """Degrees between two view directions."""
    import numpy as np
    d1, d2 = _basis(a1, e1)[0], _basis(a2, e2)[0]
    return float(np.degrees(np.arccos(np.clip(float(np.dot(d1, d2)), -1.0, 1.0))))


def _solve_anchors(facts: dict, specs: list, margin: float = 1.06, ref_size=None) -> dict:
    """The bench camera that puts the anchors' world points on their pixels, and the snaps.

    `facts` is what __forge.anchorFacts answered: the subject's bounding centre and radius (the
    camera is placed from them), the field of view, ortho, the canvas size, and one row per anchor
    with its world point. Pure: no page, no picture - the tests call it directly."""
    import numpy as np
    rows = {int(r.get("i", -1)): r for r in (facts.get("rows") or []) if isinstance(r, dict)}
    items, missing, warnings = [], [], []
    for k, s in enumerate(specs):
        r = rows.get(k) or {}
        at = r.get("at")
        if r.get("found") and isinstance(at, list) and len(at) == 3:
            items.append((k, s, r))
        else:
            missing.append("%s (%s)" % (s["label"], r.get("error") or "not found"))
    use = [t for t in items if t[1]["solve"]]
    if missing:
        warnings.append("ANCHORS: left out %s." % "; ".join(missing[:6]))
    if len(use) < _ANCHOR_MIN:
        return {"ok": False, "warnings": warnings,
                "error": "ANCHORS: a camera needs at least %d anchors it can find and solve from; "
                         "%d of %d were usable.%s" % (_ANCHOR_MIN, len(use), len(specs),
                                                      (" " + warnings[0]) if warnings else "")}
    c = np.asarray(facts.get("c") or [0, 0, 0], float)
    radius = float(facts.get("radius") or 1.0)
    ortho = bool(facts.get("ortho"))
    fov = float(facts.get("fov") or _FOV)
    P = np.array([t[2]["at"] for t in use], float)
    R = np.array([t[1]["px"] for t in use], float)
    free = len(use) >= _ANCHOR_ZOOM_FROM and not ortho
    n_par = 3 if free else 2

    # A coarse sweep of the whole sphere first: least squares from one start finds the nearest
    # basin, and the nearest basin is often the mirror image of the right answer.
    ag, eg = np.meshgrid(np.arange(0.0, 360.0, 5.0), np.arange(-40.0, 86.0, 5.0), indexing="ij")
    ag, eg = ag.ravel(), eg.ravel()
    cand = []
    for z in ((0.5, 0.7, 1.0, 1.4, 2.0, 2.8) if free else (1.0,)):
        cost = _anchor_cost(P, R, c, radius, ag, eg, np.full(ag.shape, z), margin, ortho, fov)
        for i in np.argsort(cost)[:60]:
            if np.isfinite(cost[i]):
                cand.append((float(cost[i]), float(ag[i]), float(eg[i]), z))
    if not cand:
        return {"ok": False, "warnings": warnings,
                "error": "ANCHORS: no camera round the subject sees every anchor in front of it"}
    cand.sort()
    seeds = []
    for cst, a, e, z in cand:
        if all(_view_gap(a, e, s[1], s[2]) >= 15.0 for s in seeds):
            seeds.append((cst, a, e, z))
        if len(seeds) >= 6:
            break

    def f(p):
        z = math.exp(p[2]) if free else 1.0
        if not -89.0 <= p[1] <= 89.0 or not 0.25 <= z <= 4.0:
            return float("inf")
        return float(_anchor_cost(P, R, c, radius, p[0], p[1], z, margin, ortho, fov)[0])

    sols = []
    for _cst, a, e, z in seeds:
        x0 = [a, e, math.log(z)] if free else [a, e]
        x, v = _nelder_mead(f, x0, [4.0, 4.0, 0.2][:n_par])
        x, v = _nelder_mead(f, x, [0.4, 0.4, 0.02][:n_par])
        sols.append((v, x))
    sols.sort(key=lambda t: t[0])
    bv, bx = sols[0]
    # ROUNDED TO THE VIEW STRING, and everything below is measured on THAT camera: the answer is a
    # view an agent renders, so the errors and the snaps must be the ones that view has.
    az = round(float(bx[0]) % 360.0, 2) % 360.0
    el = round(float(bx[1]), 2)
    zoom = round(math.exp(float(bx[2])), 3) if free else 1.0
    view = "az=%g,el=%g,zoom=%g" % (az, el, zoom)

    allP = np.array([t[2]["at"] for t in items], float)
    allR = np.array([t[1]["px"] for t in items], float)
    u, v, zc, eye, (dv, xv, yv) = _cam_project(allP, c, radius, az, el, zoom, margin, ortho, fov)
    q = np.stack([u, -v], -1)
    solve_mask = np.array([t[1]["solve"] for t in items])
    sim = _similarity(q[solve_mask], allR[solve_mask])
    if sim is None:
        return {"ok": False, "warnings": warnings,
                "error": "ANCHORS: the anchors land on one point in the bench's picture"}
    s, tv = sim
    err = np.linalg.norm(s * q + tv - allR, axis=1)
    per = [None] * len(specs)
    for j, (k, _s, _r) in enumerate(items):
        per[k] = round(float(err[j]), 2)
    used_err = err[solve_mask]
    wj = int(np.argmax(np.where(solve_mask, err, -1.0)))
    mean_px = round(float(used_err.mean()), 2)

    # HOW WELL THE ANCHORS PIN IT: one pixel of error in an anchor moves the answer this much.
    def resid(p):
        z = math.exp(p[2]) if free else 1.0
        uu, vv, *_ = _cam_project(P, c, radius, p[0], p[1], z, margin, ortho, fov)
        qq = np.stack([uu, -vv], -1)
        sm = _similarity(qq, R)
        return None if sm is None else (sm[0] * qq + sm[1] - R).ravel()

    p0 = np.array([az, el, math.log(zoom)] if free else [az, el], float)
    sigma = {}
    try:
        cols = []
        for k in range(n_par):
            h = 0.01 if k < 2 else 0.001
            dp = np.zeros(n_par)
            dp[k] = h
            rp, rn = resid(p0 + dp), resid(p0 - dp)
            cols.append((rp - rn) / (2.0 * h))
        J = np.stack(cols, 1)
        cov = np.linalg.inv(J.T @ J)
        sg = np.sqrt(np.clip(np.diag(cov), 0.0, None))
        sigma = {"az": round(float(sg[0]), 2), "el": round(float(sg[1]), 2)}
        if free:
            sigma["zoom_pct"] = round(float((math.exp(sg[2]) - 1.0) * 100.0), 1)
    except Exception:
        sigma = {"az": float("inf"), "el": float("inf")}

    def thin(pts):
        pts = np.asarray(pts, float)
        sv = np.linalg.svd(pts - pts.mean(0), compute_uv=False)
        return float(sv[1] / sv[0]) if sv[0] > 1e-12 else 0.0

    thin3, thin2 = thin(P), thin(R)
    rms = lambda v: math.sqrt(max(0.0, v) / len(use))           # noqa: E731
    second = None
    for v_, x_ in sols[1:]:
        if _view_gap(x_[0], x_[1], bx[0], bx[1]) >= 20.0 and np.isfinite(v_):
            second = {"view": "az=%g,el=%g" % (round(float(x_[0]) % 360.0, 1), round(float(x_[1]), 1)),
                      "mean_px": round(rms(v_), 2)}
            break
    dof = 2 * len(use) - (n_par + 3)

    per_px = ("one pixel of error in an anchor moves the answer %.1f degrees in az and %.1f in el"
              % (sigma.get("az", 0.0), sigma.get("el", 0.0)))
    if thin3 < _ANCHOR_THIN:
        warnings.append("ANCHORS: poorly conditioned - the anchors lie close to one line in the "
                        "model (%.2f of their length across it), so the camera can turn about that "
                        "line without moving them; %s. Add an anchor off it (an ear tip, a hand, "
                        "a foot)." % (thin3, per_px))
    elif thin2 < _ANCHOR_THIN:
        warnings.append("ANCHORS: poorly conditioned - the anchors lie close to one line in the "
                        "picture (%.2f of their length across it), so the side the camera stands "
                        "on rests on a few pixels of offset between them; %s. Add an anchor well "
                        "to the left or right of that line." % (thin2, per_px))
    elif max(sigma.get("az", 0.0), sigma.get("el", 0.0)) > _ANCHOR_SLOPPY:
        warnings.append("ANCHORS: poorly conditioned - %s. Add an anchor away from the others."
                        % per_px)
    if dof <= 0:
        warnings.append("ANCHORS: no anchor to spare - with this many the fit is exact by "
                        "construction, so its error says nothing. Add one more.")
    if second and second["mean_px"] <= max(1.5 * rms(bv), rms(bv) + 1.0):
        warnings.append("ANCHORS: a second camera fits almost as well - %s at %.1f px against "
                        "%.1f px. Add an anchor only one of them can explain: one on the "
                        "subject's own left or right, not on its centre line."
                        % (second["view"], second["mean_px"], rms(bv)))
    for k, sp, r in items:
        nm = [x for x in (r.get("names") or []) if x]
        if isinstance(sp["at"], dict) and len(set(nm)) > 1 and int(r.get("nodes") or 0) > 1:
            warnings.append("ANCHORS: `%s` matched %s - an anchor is one point, so name one part."
                            % (sp["at"]["part"], ", ".join(nm[:4])))
        if isinstance(sp["at"], dict) and sp["at"]["where"] not in _ANCHOR_WHERE:
            warnings.append("ANCHORS: where \"%s\" is none of top, bottom, left, right, front, back, "
                            "centre (left is the subject's own left, +X) - the centre was used."
                            % sp["at"]["where"])

    # SNAP: the world move that puts each part's anchor on its pixel at the depth it has now.
    #
    # A CENTRED PART NEVER MOVES SIDEWAYS. Both goblin builders slid the buckle sideways onto the
    # pixels of a three-quarter picture - where a centred buckle already sits right of the belt's
    # screen centre - and the user saw it from the front. So a part on the midline (no twin, and
    # either on it, straddling it, or named like one) is carried back onto it and then moved only
    # in y and z: its pixel's ray is met where it crosses that plane. When that is a long way from
    # where the part is, the camera is what is wrong, and the answer says so instead of a move.
    from .live_detail import side_key
    height = float(facts.get("height") or 0.0) or 2.0 * radius
    mid = float(facts.get("mid") or 0.0)
    snap = []
    for j, (k, sp, r) in enumerate(items):
        if not isinstance(sp["at"], dict):
            continue
        P0 = allP[j]
        qs = (allR[j] - tv) / s
        us, vs = float(qs[0]), float(-qs[1])
        z_ = float(zc[j])
        if ortho:
            o_, rd = eye + xv * us + yv * vs, -dv
            at_depth = o_ + dv * z_
        else:
            o_, rd = eye, xv * us + yv * vs - dv
            at_depth = eye + rd * -z_
        mv = at_depth - P0
        entry = {"anchor": sp["n"], "part": sp["at"]["part"], "where": r.get("where") or sp["at"]["where"],
                 "solve": sp["solve"], "error_px": round(float(err[j]), 2)}
        name = sp["at"]["part"]
        lo_, hi_ = r.get("lo"), r.get("hi")
        has_box = isinstance(lo_, list) and isinstance(hi_, list) and len(lo_) == 3 and len(hi_) == 3
        cx = (float(lo_[0]) + float(hi_[0])) / 2.0 if has_box else float(P0[0])
        sided = bool(side_key(name)) or any(side_key(n) for n in (r.get("names") or []))
        midline = not sided and not _name_hit(name, _PROPS) and (
            abs(cx - mid) < 0.01 * height or _name_hit(name, _CENTRED)
            or (has_box and float(lo_[0]) < mid < float(hi_[0])))
        if midline:
            off_mid = mid - cx
            if sp["solve"] and abs(off_mid) > SYM_OFF * height:
                warnings.append("ANCHORS: `%s` is a centred part sitting %s %s of the midline - as "
                                "a camera anchor it pulls the camera toward that mistake. Put it back "
                                "on x = %g first, or give it solve:false."
                                % (name, _cm(off_mid), "left" if cx > mid else "right", round(mid, 4)))
            # Back onto the midline only when it is off it by more than a hair (0.5% of the figure).
            dx = off_mid if abs(off_mid) >= 0.005 * height else 0.0
            reach = None
            if abs(float(rd[0])) > 1e-9:
                t = (float(P0[0]) + dx - float(o_[0])) / float(rd[0])
                if t > 0:
                    reach = o_ + rd * t - P0
                    reach[0] = dx
            yz = math.hypot(float(reach[1]), float(reach[2])) if reach is not None else float("inf")
            entry["centred"] = True
            # Within the midline plane a move is longer than the free one by 1/sin of the angle
            # the plane is seen at - 2.7x from az=338 - so 4x the free move is still a y/z fix;
            # past that, or past 8% of the figure, it is the camera that is wrong.
            if reach is not None and yz <= max(4.0 * float(np.linalg.norm(mv)), 0.005 * height) \
                    and yz <= 0.08 * height:
                mv = reach
            else:
                # Seen from the camera: the part as it WOULD be, on the midline, against its pixel.
                a_ = P0 + np.array([dx, 0.0, 0.0]) - eye
                cosang = float(np.dot(a_, rd) / (np.linalg.norm(a_) * np.linalg.norm(rd) or 1.0))
                deg = math.degrees(math.acos(max(-1.0, min(1.0, cosang))))
                entry["camera_off_deg"] = round(deg, 1)
                entry["note"] = ("the camera is off by ~%.1f° - solve it with anchors; do not move "
                                 "a centred part sideways" % deg)
                mv = np.array([dx, 0.0, 0.0])
        entry["move"] = [round(float(x), 6) for x in mv]
        snap.append(entry)
    snap.sort(key=lambda x: -x["error_px"])

    H = float((facts.get("canvas") or [640, 480])[1])
    frame = {"scale": round(s, 3), "centre_px": [round(float(tv[0]), 2), round(float(tv[1]), 2)]}
    if ref_size and not ortho:
        frame["fov_deg"] = round(math.degrees(2.0 * math.atan(float(ref_size[1]) / 2.0 / s)), 2)
    return {"ok": True, "view": view, "az": az, "el": el, "zoom": zoom,
            "zoom_held": not free, "ortho": ortho, "margin": margin,
            "error_px": per, "mean_px": mean_px,
            "worst": {"anchor": items[wj][1]["n"], "label": items[wj][1]["label"],
                      "px": round(float(err[wj]), 2)},
            "used": len(use), "anchors": len(specs), "dof": dof, "frame": frame,
            "sigma_deg_per_px": sigma, "thin": {"model": round(thin3, 3), "picture": round(thin2, 3)},
            "second": second, "snap": snap, "warnings": warnings,
            # For the page check, not for the caller: the found anchors, and where they should land.
            "points": [list(map(float, p)) for p in allP],
            "uv": [[float(a), float(b)] for a, b in zip(u, v)],
            "canvas_h": H}


def _anchor_check(sol: dict, got) -> Optional[float]:
    """How far the page's own camera puts the anchors from where the solve says - pixels, worst."""
    if not isinstance(got, dict) or not got.get("px"):
        return None
    try:
        W, H = float(got["canvas"][0]), float(got["canvas"][1])
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    if sol.get("ortho"):
        return None             # the ortho frame's half-height is the page's; not re-derived here
    f = H / (2.0 * math.tan(math.radians(_FOV) / 2.0))
    worst = 0.0
    for (u, v), p in zip(sol.get("uv") or [], got["px"]):
        if not p:
            return None
        worst = max(worst, math.hypot(W / 2.0 + u * f - float(p[0]), H / 2.0 - v * f - float(p[1])))
    return round(worst, 4)


async def _anchor_pass(live, specs: list, margin: float, ref_size=None) -> dict:
    """Resolve the anchors in the page, solve, check the page's camera agrees, fetch the edits."""
    facts = await live.ask("__forge.anchorFacts(%s)" % json.dumps([{"at": s["at"]} for s in specs]),
                           depth=6)
    if not isinstance(facts, dict) or not facts.get("ok"):
        return {"ok": False, "error": "ANCHORS: %s" % ((facts or {}).get("error") if isinstance(facts, dict)
                                                       else "the page could not resolve them")}
    sol = _solve_anchors(facts, specs, margin, ref_size)
    if not sol.get("ok"):
        return sol
    await live.raw("__forge.view(%s,%s)" % (json.dumps(sol["view"]), float(margin)))
    got = await live.ask("__forge.project(%s)" % json.dumps(sol.get("points") or []), depth=6)
    sol["check_px"] = _anchor_check(sol, got)
    for sn in sol.get("snap") or []:
        if all(abs(float(x)) < 1e-7 for x in sn["move"]):
            continue                    # nothing to move: a centred part the camera must fix
        ed = await live.ask("__forge.snapEdit(%s,%s)" % (json.dumps(sn["part"]), json.dumps(sn["move"])),
                            depth=4)
        found = int((ed or {}).get("found") or 0) if isinstance(ed, dict) else 0
        if found == 1:
            sn["edit"] = {"target": sn["part"], "move": ed.get("move")}
        elif found > 1:
            sn["note"] = ("`%s` moves %d nodes (%s) - name one" %
                          (sn["part"], found, ", ".join(str(x) for x in (ed.get("names") or [])[:4])))
    return sol


def _anchor_public(sol: dict) -> dict:
    """The solve as the caller gets it: no internals."""
    keep = ("view", "az", "el", "zoom", "zoom_held", "ortho", "margin", "error_px", "mean_px",
            "worst", "used", "anchors", "dof", "frame", "sigma_deg_per_px", "thin", "second",
            "check_px", "overlap")
    return {k: sol[k] for k in keep if k in sol}


def _anchor_lines(sol: dict) -> list:
    """The findings for a solved camera: the answer, which way it faces, what is doubtful, snaps."""
    w = sol.get("worst") or {}
    held = (" The zoom is held at 1, the bench's own framing distance: three anchors fix the angle "
            "and the framing, and a fourth is what measures perspective."
            if sol.get("zoom_held") and not sol.get("ortho") else "")
    lines = ["SOLVED: the reference was taken from %s - %d anchor%s, %.1f px off on average in the "
             "reference's own pixels, worst %.1f px (%s). Render it with views=[\"%s\"].%s"
             % (sol["view"], sol["used"], "" if sol["used"] == 1 else "s", sol["mean_px"],
                float(w.get("px") or 0.0), w.get("label") or "?", sol["view"], held),
             FACING]
    lines.extend(sol.get("warnings") or [])
    shown = 0
    for sn in sol.get("snap") or []:
        if shown >= 6 or float(sn.get("error_px") or 0.0) < 1.0:
            continue
        mv = sn["move"]
        head = "SNAP: `%s` %s is %.1f px from its pixel" % (sn["part"], sn.get("where") or "",
                                                           float(sn["error_px"]))
        if sn.get("camera_off_deg") is not None:
            back = ""
            if sn.get("edit") and abs(float(mv[0])) > 1e-4:
                back = " edits:[%s] only puts it back on the midline." % json.dumps(sn["edit"])
            lines.append("%s, and it is a centred part: %s.%s" % (head, sn["note"], back))
        elif sn.get("edit"):
            lines.append("%s; %sedits:[%s] puts it there%s (a world move of [%.4f, %.4f, %.4f] m)."
                         % (head, "it is a centred part, so it stays on the midline and moves in y "
                                  "and z only: " if sn.get("centred") else "",
                            json.dumps(sn["edit"]), "" if sn.get("centred") else " at its present depth",
                            mv[0], mv[1], mv[2]))
        else:
            lines.append("%s; %s (a world move of [%.4f, %.4f, %.4f] m)."
                         % (head, sn.get("note") or "no single node to move", mv[0], mv[1], mv[2]))
        shown += 1
    return lines


@_settles
def aim(project: str, js: str = "", steps: int = 12, els: Optional[list] = None,
        views: Optional[list] = None, ref: str = "", width: int = 420, height: int = 420,
        engine: str = "", background: str = "", ortho=None, light: str = "",
        env=None, ambient=None, exposure=None, lights: Optional[dict] = None, shadows=None,
        ref_mask: str = "", ref_crop: str = "", facts: bool = False,
        anchors: Optional[list] = None) -> dict:
    """Find the camera angle whose silhouette matches the reference best. Numbers only.

    Pass `views` to score exactly those angles instead of sweeping. That is how a coarse sweep is
    refined: sweep once, then score a few angles either side of the winner.

    Pass `anchors` - [{"at": [x, y, z] | {"part": "helmet", "where": "top"}, "px": [x, y]}, ...],
    px in the reference's own pixels, at least three - and the camera is SOLVED instead of swept:
    az, el and zoom by least squares over the projected anchors, with `snap` moves that put each
    named part's anchor on its pixel. A silhouette cannot tell a front from a back; points can."""
    bad = _guard(project, forge=True)
    if bad:
        return bad
    anchor_specs, anchor_bad = _anchor_specs(anchors) if anchors else ([], [])
    if anchors and len([s for s in anchor_specs if s["solve"]]) < _ANCHOR_MIN:
        return {"ok": False,
                "error": "ANCHORS: a camera needs at least %d anchors to solve from; got %d usable%s"
                         % (_ANCHOR_MIN, len([s for s in anchor_specs if s["solve"]]),
                            (" - " + "; ".join(anchor_bad[:4])) if anchor_bad else "")}
    # NO js MEANS THE BENCH THAT IS ALREADY THERE. `look` has always worked that way and `aim`
    # did not, so an agent that had just built something had to hand the same code over a second
    # time to find out which way to point the camera at it.
    reuse = not str(js).strip()
    if reuse and not (_entry(project, create=False) or {}).get("target"):
        return {"ok": False, "error": "nothing to aim at. Pass js to build it, or build it once "
                                      "with /api/live/forge and call aim again with no js."}
    if _mask_auto(ref_mask):
        ref_mask = ""
    if ref_mask or ref_crop:
        reference(project, ref if ref and (ref_mask or ref_crop) else "", ref_mask, ref_crop)
        ref = ""                                # already remembered, crop and all
    got_ref = reference(project, ref) if ref else reference(project)
    ref_path = got_ref.get("reference") or ""
    if not ref_path:
        return {"ok": False,
                "error": "no reference for this project. Pass ref=<image path> once; it is kept."}
    ref_size = None
    if anchor_specs:
        cand = []
        try:
            from PIL import Image as _Img
            with _Img.open(ref_path) as _im:
                ref_size = _im.size
        except Exception:
            ref_size = None
    elif views:
        cand = [w for w in (_view_ok(v) for v in views) if w]
    else:
        n = max(4, min(36, int(steps or 12)))
        el_list = [float(e) for e in (els or [-5, 10, 25])][:5]
        cand = ["az=%d,el=%g" % (round(360.0 * i / n), e) for e in el_list for i in range(n)]
    if not cand and not anchor_specs:
        return {"ok": False, "error": "no angle to score"}

    _doing(project, "aiming", ("solving the camera from %d anchors" % len(anchor_specs))
           if anchor_specs else "sweeping %d angles against the reference" % len(cand))
    e = _entry(project)
    want_url = ""
    if _origin_gone(e):
        e["origin_restarted"] = e.get("url")
        e["url"] = ""
    if not e.get("url"):
        try:
            origin, how = origin_for(Path(project), start_dev=True)
            want_url = origin.rstrip("/") + "/"
            if how:
                e["how"] = how
        except Exception as ex:
            return {"ok": False, "error": "could not find a way to serve the project: %s" % ex}
    # No ground and no sky. The silhouette pass paints every mesh in the scene black, so a floor
    # would become part of the shape being measured.
    background = _canvas(e, 0, 0, background)[2]
    look_now = _look_state(e, _studio_opts(ortho, light, env, ambient, exposure, lights, shadows))
    opts = json.dumps(dict({"width": int(width), "height": int(height), "background": background,
                            "ground": False, "engine": engine or "", "sky": False}, **look_now))
    body = ("(async()=>{const __c=__forge.ctx();"
            "const {pc,THREE,engine,app,device,renderer,scene,camera,root,forge,add,clear,log,"
            "params}=__c;"
            "let __e;try{await (async()=>{%s})();}catch(err){__e=String(err&&err.stack||err);}"
            "return JSON.stringify({e:__e});})()" % js)
    heal: dict = {"project": project}

    async def go():
        ws, live = await _session(e, want_url=want_url, wait_ms=1500 if want_url else 0)
        try:
            if want_url:
                await _bridge(live, e)
                e["url"] = want_url
            await _forge_script(live)
            if reuse:
                # Do NOT call ensure: it disposes the bench whenever the size differs, which is
                # exactly the scene this call was asked to look at.
                if not await live.raw("!!window.__forge"):
                    return {"built": {"ok": False, "error": "the page has been reloaded"}}
                pre = await live.ask("__forge.stats()", depth=4) or {}
                if not pre.get("meshes") and not pre.get("flat"):
                    return {"built": {"ok": False, "error": "the bench is empty - pass js"}}
                built, ran = {"ok": True, "reused": True}, {}
                if look_now:
                    await live.ask("__forge.studio(%s)" % json.dumps(look_now), depth=2)
            else:
                built = await live.ask("__forge.ensure(%s)" % opts)
                if not (built or {}).get("ok"):
                    return {"built": built}

                async def attempt():
                    await live.raw("__forge.clear()")
                    await live.raw("__forge.params = {}")
                    got = json.loads(await live.raw(body) or "{}")
                    return got, got.get("e") or ""
                ran = await _healing(live, e, opts, attempt, heal)
            if anchor_specs:
                if (ran or {}).get("e"):
                    return {"built": built, "ran": ran}
                # THE CAMERA, SOLVED - then one silhouette from it, so the answer still carries the
                # overlap an agent is used to reading, and the camera is left on the answer.
                sol = await _anchor_pass(live, anchor_specs, 1.06, ref_size)
                sil = ""
                if sol.get("ok"):
                    await live.raw("__forge.setPass('silhouette')")
                    sil = await live.raw("__forge.view(%s,1.06)" % json.dumps(sol["view"]))
                    await live.raw("__forge.clearPass()")
                    await live.raw("__forge.view(%s,1.06)" % json.dumps(sol["view"]))
                stats = await live.ask("__forge.stats()", depth=8) or {}
                return {"built": built, "ran": ran, "solved": sol, "sil": sil, "stats": stats}
            await live.raw("__forge.setPass('silhouette')")
            shots = []
            for v in cand:
                shots.append((v, await live.raw("__forge.view(%s,1.06)" % json.dumps(v))))
            await live.raw("__forge.clearPass()")
            stats = await live.ask("__forge.stats()", depth=8) or {}
            return {"built": built, "ran": ran, "shots": shots, "stats": stats}
        finally:
            await ws.close()

    try:
        got = _run(go)
    except Exception as ex:
        return {"ok": False, "error": str(ex).strip()[:1200]}
    built = got.get("built") or {}
    if not built.get("ok"):
        return {"ok": False, "error": built.get("error") or "the forge could not start"}
    healed = _heal_report(heal)
    if (got.get("ran") or {}).get("e"):
        out = {"ok": False, "error": (got["ran"]["e"])[:1200]}
        if healed:
            out["heal"] = healed
        return out

    try:
        target = _mask_of_reference(ref_path, got_ref.get("mask") or "")
    except Exception as ex:
        return {"ok": False, "error": "could not read the reference: %s" % str(ex)[:200]}

    if anchor_specs:
        sol = got.get("solved") or {}
        if not sol.get("ok"):
            out = {"ok": False, "error": sol.get("error") or "ANCHORS: the camera could not be solved"}
            if sol.get("warnings"):
                out["findings"] = list(sol["warnings"])
            return out
        sil = _decode_shot(got.get("sil") or "")
        cmp = _compare_masks(_mask_of_render(sil), target) if sil is not None else None
        if cmp:
            sol["overlap"] = cmp["overlap"]
        public = _anchor_public(sol)
        res = {"ok": True, "reference": ref_path, "solved": public, "snap": sol.get("snap") or [],
               "best": {"view": sol["view"], "overlap": (cmp or {}).get("overlap"),
                        "width_ratio": (cmp or {}).get("width_ratio")},
               "stats": {k: (got.get("stats") or {}).get(k)
                         for k in ("triangles", "meshes", "materials", "solidity")}}
        if ref_size:
            res["reference_size"] = list(ref_size)
        lines = _anchor_lines(sol)
        if cmp:
            lines.insert(1, "MATCH: from the solved camera the silhouette covers %.2f of the "
                            "reference's - read beside the pixel error above, which is the number "
                            "that decides the angle." % cmp["overlap"])
        if anchor_bad:
            lines.append("ANCHORS: ignored %s." % "; ".join(anchor_bad[:4]))
        res["findings"] = _heal_finding(healed) + lines
        if healed:
            res["heal"] = healed
        _done(project, "aimed", "solved %s, %.1f px" % (sol["view"], sol["mean_px"]))
        return res

    from PIL import Image
    import io as _io
    scored = []
    for name, data in (got.get("shots") or []):
        if not isinstance(data, str) or not data.startswith("data:image"):
            continue
        try:
            im = Image.open(_io.BytesIO(base64.b64decode(data.split(",", 1)[1])))
            cmp = _compare_masks(_mask_of_render(im), target)
        except Exception:
            continue
        if cmp:
            cmp["view"] = name
            scored.append(cmp)
    if not scored:
        return {"ok": False, "error": "nothing was drawn at any angle - is anything add()ed?"}
    scored.sort(key=lambda c: c["overlap"], reverse=True)
    best = scored[0]
    res = {"ok": True, "reference": ref_path, "scored": len(scored),
           "best": {"view": best["view"], "overlap": best["overlap"],
                    "width_ratio": best["width_ratio"]},
           "ranked": [{"view": c["view"], "overlap": c["overlap"]} for c in scored[:5]],
           "bands": best["bands"],
           "stats": {k: (got.get("stats") or {}).get(k)
                     for k in ("triangles", "meshes", "materials", "solidity")}}
    lines = ["AIM: the best angle is %s - silhouette overlap %.2f of 1.00, and at the same height "
             "the subject is %.2f times the reference's width. Render it with views=[%s]."
             % (best["view"], best["overlap"], best["width_ratio"], json.dumps(best["view"]))]
    lines.append(FACING)
    twin = _mirror_finding(scored)
    if twin:
        lines.insert(1, twin)
    band = _band_finding(best)
    if band:
        lines.append("SHAPE: %s. Both are measured on the silhouette at the matched angle, so "
                     "this is proportion, not lighting." % band)
    if best["overlap"] < 0.55:
        lines.append("The best angle still overlaps only %.2f. Either the proportions are a long "
                     "way out, or the reference holds a pose the geometry does not - a raised "
                     "limb or a turned head is a pose decision, not a shape error."
                     % best["overlap"])
    res["findings"] = _heal_finding(healed) + lines
    if healed:
        res["heal"] = healed
    _done(project, "aimed", "best %s, overlap %.2f" % (best["view"], best["overlap"]))
    return res


# ---------------------------------------------------------------------------
# Thumbnails for a project's own assets
#
# A shelf of thirty creatures that all show the same table icon is a list, not a library. The
# thing that makes it a library is seeing them, and the Studio already has everything needed to
# see them: the project's page, its engine, and its own builder functions. So a thumbnail is not a
# picture someone has to remember to make — it is the asset, built by the code that will build it
# at run time, rendered once and kept.
#
# Cached on the file's own mtime: change the builder and the picture is stale by definition, and
# nothing else needs to invalidate it.
# ---------------------------------------------------------------------------

_THUMB_VIEW = "az=32,el=14"


def _assetopen_stamp() -> str:
    """The built `asset-open.js`'s mtime, as a cache key.

    Without it, a tab that imported the module once keeps that copy for its whole life — so a fix
    inside the module reaches nobody with a page already open, which is every page.
    """
    try:
        p = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist" / "asset-open.js"
        return str(int(p.stat().st_mtime))
    except OSError:
        return "0"


def _thumb_js(item: dict, project: str = "", origin: str = "") -> str:
    """The lines a person would write to look at one entry.

    ALL of it lives in one module now — `asset-open.js`, the same file the editor's viewport
    imports. There used to be a copy of the convention here, written as a Python string, and it
    disagreed with the editor's copy three separate times: five thumbnails that reported success
    and drew nothing, a preview built by the wrong function, a table that could not be found
    because the game never exported it. Two implementations of one convention is not a
    convention. What is left here is the call, and the page's own way of loading a model file,
    which is the one genuinely per-page part.
    """
    try:
        port = int(settings.get("port", 8777) or 8777)
    except Exception:
        port = 8777
    ref = {k: item.get(k) for k in
           ("type", "name", "file", "root", "export", "table", "key", "index", "model",
            "deps", "path")
           if item.get(k) is not None}
    # WHERE TO ASK WHEN NO DEV SERVER HAS IT. See `importCandidates`: last candidate, and the
    # only one that reaches a file outside every game's root.
    ref["studio"] = _self_base()
    # THE MODEL COMES FROM THE STUDIO. `openAsset` loads `ref.model` before it looks for a
    # builder, and a project-relative path there is fetched from the PAGE's origin - which for a
    # workspace model is a dev server that has never heard of it and answers index.html.
    if str(item.get("type")) == "model" and item.get("path"):
        ref["model"] = _model_url(str(item["path"]))
    # AND A MODEL NAMED BY A SPEC, which is the same fault one level in. A `BRAINROT_ASSETS` row
    # carries `model: "rot-rush/src/assets/brainrots/x.glb"` - relative to the WORKSPACE, not to
    # the game's dev server - so the page fetched it from an origin that has never heard of it and
    # got index.html back. 23 of them failed with `Invalid magic number ... found 0x6f64213c`,
    # which is the four bytes `<!do`.
    elif item.get("model"):
        # AGAINST EVERY BASE IT COULD BE RELATIVE TO, because the caller and the index disagree
        # about which "project" means. `_thumb_js` is handed the SUB-project the asset belongs to;
        # a spec's `model` is relative to the WORKSPACE above it. Joining those two gives
        # `<workspace>/rot-rush/rot-rush/src/...`, which exists nowhere, so the whole branch fell
        # through in silence and 23 models went on being fetched from a dev server that answers
        # index.html for anything it has not heard of. Try, do not assume.
        _rel = str(item["model"]).replace("\\", "/").lstrip("/")
        _bases = []
        try:
            _p = Path(project) if project else None
            if _p:
                _bases += [_p, _p.parent]
                if item.get("root"):
                    _bases.append(_p / str(item["root"]))
            _ip = Path(str(item.get("path") or ""))
            if _ip.name:
                _bases += list(_ip.parents)[:6]
        except (OSError, ValueError):
            _bases = []
        for _b in _bases:
            try:
                _m = _b / _rel
                if _m.is_file():
                    ref["model"] = _model_url(str(_m))
                    break
            except OSError:
                continue
    return (
        "// A MODEL IS LOADED BY THE PAGE'S OWN ENGINE, so what appears is what the game will\n"
        "// show. PlayCanvas loads a container and instantiates it; three.js pulls the loader\n"
        "// from the project's own dependency, which its dev server already serves.\n"
        "const __loadModel = async (url) => {\n"
        "  const u = /^(https?:|\\/)/.test(url) ? url : '/' + String(url).replace(/^\\.?\\//, '');\n"
        "  if (typeof pc !== 'undefined' && pc && typeof app !== 'undefined' && app) {\n"
        "    const asset = await new Promise((res, rej) => {\n"
        "      app.assets.loadFromUrl(u, 'container', (e, a) => (e ? rej(new Error(String(e))) : res(a)));\n"
        "    });\n"
        "    if (!asset || !asset.resource) throw new Error('the engine loaded ' + u +\n"
        "      ' and there was nothing inside it - the server probably answered a page, not a"
        " model');\n"
        "    return asset.resource.instantiateRenderEntity();\n"
        "  }\n"
        "  const { GLTFLoader } = await import('three/examples/jsm/loaders/GLTFLoader.js');\n"
        "  const gltf = await new Promise((res, rej) => new GLTFLoader().load(u, res, undefined, rej));\n"
        "  return gltf.scene;\n"
        "};\n"
        # A PAGE REMEMBERS A SUCCESSFUL IMPORT TOO. The retry-under-a-fresh-query rule lives
        # INSIDE this module, and the tab imported the module once — before that rule existed —
        # and holds that copy for as long as it lives. Sixty assets went on reporting "could not
        # import" from code that had already been fixed. The built file's mtime is the version.
        "const __AO = await import('http://127.0.0.1:%d/asset-open.js?v=%s');\n"
        "const __r = await __AO.openAsset(%s, {\n"
        "  THREE: typeof THREE !== 'undefined' ? THREE : undefined,\n"
        "  pc: typeof pc !== 'undefined' ? pc : undefined,\n"
        "  device, loadModel: __loadModel,\n"
        "  // THE GAME'S OWN ORIGIN, SPELLED OUT. This was [''], with the note that this\n"
        "  // page IS the owning game's dev server - true of the PAGE, false of the MODULE.\n"
        "  // openAsset lives in asset-open.js, which the page fetches from the Studio, and\n"
        "  // a bare /src/render/x.ts inside a module resolves against THAT module's base\n"
        "  // URL. So every candidate went to the Studio on :8777 and 404ed, while the same\n"
        "  // path imported by hand from the page's own scope loaded from :5179.\n"
        "  bases: [%s],\n"
        "});\n"
        "if (!__r.object) throw new Error(__r.tried.slice(0, 3).join('; ') || 'nothing built this');\n"
        "add(__r.object);\n"
        # COUNT WHAT ARRIVED. An `_idle.glb` holds animation and no mesh at all, and a blank
        # frame from that is a correct answer - but it read exactly like a builder that threw.
        # The count is free here and impossible to get later.
        "let __t = 0, __m = 0;\n"
        "try {\n"
        "  const o = __r.object;\n"
        "  if (o && o.traverse) o.traverse((n) => { const g = n && n.geometry;\n"
        "    if (!g) return; __m++;\n"
        "    __t += g.index ? g.index.count / 3 : (g.attributes && g.attributes.position\n"
        "      ? g.attributes.position.count / 3 : 0); });\n"
        "  else if (o && o.findComponents) o.findComponents('render').forEach((c) => {\n"
        "    (c.meshInstances || []).forEach((mi) => { __m++;\n"
        "      __t += (mi.mesh && mi.mesh.primitive && mi.mesh.primitive[0]\n"
        "        ? mi.mesh.primitive[0].count / 3 : 0); }); });\n"
        "} catch (e) {}\n"
        "return JSON.stringify({ how: __r.how, tris: Math.round(__t), meshes: __m });"
        % (port, _assetopen_stamp(), json.dumps(ref),
           json.dumps(str(origin or "").rstrip("/")))
    )


def _ink(im) -> float:
    """How much of the frame differs from its own backdrop. The corners are the backdrop."""
    import numpy as np
    a = np.asarray(im, dtype=np.int16)
    h, w, _ = a.shape
    k = max(2, min(h, w) // 12)
    corners = np.concatenate([a[:k, :k].reshape(-1, 3), a[:k, -k:].reshape(-1, 3),
                              a[-k:, :k].reshape(-1, 3), a[-k:, -k:].reshape(-1, 3)])
    bg = np.median(corners, axis=0)
    return float((np.abs(a - bg).sum(axis=2) > 30).mean())


def _thumb_file(project: str, item: dict, size: int) -> Path:
    stamp = 0.0
    try:
        stamp = Path(str(item.get("path") or "")).stat().st_mtime
    except OSError:
        pass
    key = "%s|%s|%d|%d" % (item.get("id"), item.get("file"), size, int(stamp))
    import hashlib
    h = hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:16]
    return _LIVE_DIR / _slug(project) / "thumbs" / (h + ".png")


def thumbs(project: str, ids: Optional[list] = None, size: int = 288,
           limit: int = 120, engine: str = "") -> dict:
    """Render one picture per asset, in one browser session, and keep them.

    Batched on purpose: opening the project's page is the expensive part and building one creature
    is milliseconds, so forty in one visit costs about what one costs on its own."""
    bad = _guard(project, forge=True)
    if bad:
        return bad
    try:
        from . import assets_index
        listing = assets_index.list_assets(project)
    except Exception as ex:
        return {"ok": False, "error": "could not read the project's assets: %s" % ex}
    asked = [str(i) for i in (ids or [])]
    cap = max(1, min(400, int(limit or 120)))
    want = asked[:cap]
    # SAY SO WHEN YOU DROP SOME. The route did not pass `limit` at all, so a caller who asked for
    # 352 got 120 answers and silence about the other 232 - no picture, no error, nothing to
    # retry. A ceiling is fine; a ceiling nobody is told about is not.
    dropped = asked[cap:]
    by_id = {i["id"]: i for i in (listing.get("items") or [])}
    items = [by_id[i] for i in want
             if i in by_id and by_id[i].get("type") in ("code", "spec", "model",
                                                        "image", "texture")]
    if not items:
        return {"ok": True, "thumbs": {}, "rendered": 0, "cached": 0}

    out: dict = {}
    early: dict = {}
    drawn = 0
    # AN IMAGE IS ALREADY A PICTURE. On this workspace 465 of the 816 assets are images and not
    # one of them had a preview, because the shelf only ever asked the BROWSER to draw things and
    # a PNG is the one thing that does not need one. Composited onto the same backdrop the
    # rendered thumbnails use, so a shelf of both does not look like two shelves.
    rest = []
    for it in items:
        if str(it.get("type")) not in ("image", "texture"):
            rest.append(it)
            continue
        f = _thumb_file(project, it, size)
        if f.is_file():
            out[it["id"]] = str(f)
            continue
        try:
            from PIL import Image as _Im
            im = _Im.open(str(it.get("path") or ""))
            im.thumbnail((int(size), int(size)), _Im.LANCZOS)
            plate = _Im.new("RGB", im.size, (22, 26, 34))
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                plate.paste(im, (0, 0), im)
            else:
                plate.paste(im.convert("RGB"), (0, 0))
            f.parent.mkdir(parents=True, exist_ok=True)
            plate.save(f)
            out[it["id"]] = str(f)
            drawn += 1
        except Exception as ex:
            early[it["id"]] = "could not read the image: %s" % str(ex)[:120]
    items = rest
    if not items:
        return {"ok": True, "thumbs": out, "rendered": drawn,
                "cached": len(out) - drawn, "errors": early}

    todo = []
    for it in items:
        f = _thumb_file(project, it, size)
        if f.is_file():
            out[it["id"]] = str(f)
        else:
            todo.append((it, f))
    if not todo:
        return {"ok": True, "thumbs": out, "rendered": drawn, "cached": len(out) - drawn,
                "errors": early}

    # ONE VISIT PER GAME, not one per workspace. A workspace can hold several games, each with its
    # own package.json and its own dev server, and a file from one of them is simply not on
    # another's server — which is how thirty creatures failed to import from a path that looked
    # entirely reasonable.
    groups: dict = {}
    for it, f in todo:
        groups.setdefault(str(it.get("root") or ""), []).append((it, f))
    # THE RECURSION THAT ATE 112 ANSWERS. This used to call itself once per game with the
    # WORKSPACE's ids — `code:rot-rush/src/render/proplib.ts#x` — and the sub-call re-listed the
    # sub-project, where the same entry is `code:src/render/proplib.ts#x`. Not one id matched, so
    # it returned an empty answer with no error in it and the caller saw silence. The loop below
    # already visits one origin per game; the recursion was redundant as well as wrong.

    opts = json.dumps({"width": int(size), "height": int(size), "background": "#161a22",
                       "ground": False, "engine": engine or "", "sky": False})

    # ONE VISIT PER GAME, NOT PER WORKSPACE.
    #
    # A research folder holds three games — each with its own package.json, its own engine and its
    # own dev server — and a file belonging to one of them is simply not on another's server. The
    # workspace had a single origin, so twenty creatures rendered and thirty from the game next
    # door failed to import from a path that looked entirely reasonable.
    groups: dict = {}
    for it, f in todo:
        groups.setdefault(str(it.get("root") or ""), []).append((it, f))

    made, errs, heals = 0, {}, {}
    from PIL import Image
    import io as _io

    for _root, _pairs in groups.items():
        _proj = str(Path(project) / _root) if _root else project
        e = _entry(_proj)
        want_url = ""
        try:
            origin, how = origin_for(Path(_proj), start_dev=True)
            origin = origin.rstrip("/")
            if how:
                e["how"] = how
        except Exception as ex:
            for it, _f in _pairs:
                errs[it["id"]] = "no way to serve %s: %s" % (_root or "the project", str(ex)[:120])
            continue
        # AND VISIT IT WHEN THE TAB IS SOMEWHERE ELSE. `import('/src/render/proplib.ts')` resolves
        # against the PAGE's origin, not the dev server's, so a tab left on the game next door
        # imports from a host that does not have the file. Measured: that exact module answered
        # 200 text/javascript on 127.0.0.1:5179 while the import failed on all four candidate
        # paths. The visit used to be skipped whenever ANY session existed for the entry.
        here = str(e.get("url") or "")
        if not here or not here.rstrip("/").startswith(origin):
            want_url = origin + "/"
        got = _thumb_batch(e, want_url, opts, _pairs, origin)
        if got.get("heal"):
            heals[_root or "."] = got["heal"]
        if got.get("error"):
            for it, _f in _pairs:
                errs[it["id"]] = got["error"][:200]
            continue
        for it, f, img, err, built in got.get("shots") or []:
            if err or not isinstance(img, str) or not img.startswith("data:image"):
                # 200 cut the message off before "First failure:", which is the only part that
                # says WHY. An error that hides its cause costs more than the bytes it saves.
                errs[it["id"]] = (err or "no picture came back")[:420]
                continue
            try:
                im = Image.open(_io.BytesIO(base64.b64decode(img.split(",", 1)[1]))).convert("RGB")
                if _ink(im) < 0.004:
                    # AN ANSWER, NOT A FAILURE. 65 of these are `x_idle.glb` and `x_walk.glb`:
                    # animation clips with no mesh in them. A blank frame is the truth about that
                    # file, and reading it as "the builder is broken" sent people to look at code
                    # that was fine. The triangle count is what tells the two apart.
                    tris = int((built or {}).get("tris") or 0)
                    if (built or {}).get("meshes") == 0:
                        errs[it["id"]] = ("nothing to draw: this file loaded, and holds no mesh at "
                                          "all - an animation clip, a rig, or a table of numbers")
                    elif tris <= 0:
                        errs[it["id"]] = ("nothing to draw: %d object%s arrived with no geometry "
                                          "on them" % ((built or {}).get("meshes") or 0,
                                                       "" if (built or {}).get("meshes") == 1
                                                       else "s"))
                    else:
                        errs[it["id"]] = ("built %d triangles and the frame is empty - it is off "
                                          "camera, inside-out, or scaled to nothing" % tris)
                    continue
                f.parent.mkdir(parents=True, exist_ok=True)
                im.save(f)
                out[it["id"]] = str(f)
                made += 1
            except Exception as ex:                      # pragma: no cover - defensive
                errs[it["id"]] = str(ex)[:200]
    errs.update(early)
    res = {"ok": True, "thumbs": out, "rendered": made + drawn,
           "cached": len(out) - made - drawn, "errors": errs}
    if heals:
        res["heal"] = heals
    if dropped:
        res["not_asked"] = dropped
        res["note"] = ("%d of the %d ids were over the %d-per-call ceiling and were not looked at. "
                       "Ask again with those ids, or raise `limit`." % (len(dropped), len(asked), cap))
    return res


def _thumb_batch(e: dict, want_url: str, opts: str, pairs: list,
                 origin: str = "") -> dict:
    """One browser visit: build each asset in turn and keep the frame."""

    async def go():
        ws, live = await _session(e, want_url=want_url, wait_ms=1500 if want_url else 0)
        shots = []
        try:
            if want_url:
                await _bridge(live, e)
                e["url"] = want_url
            await _forge_script(live)
            built = await live.ask("__forge.ensure(%s)" % opts)
            if not (built or {}).get("ok"):
                return {"error": "the forge could not start", "shots": []}
            heal: dict = {}
            for it, f in pairs:
                body = ("(async()=>{const __c=__forge.ctx();"
                        "const {pc,THREE,engine,app,device,renderer,scene,camera,root,forge,add,"
                        "clear,log,params}=__c;"
                        "let __e,__v;try{__v=await (async()=>{%s})();}"
                        # WHERE IT WAS STANDING. An import that fails is almost always an import
                        # against the wrong origin, and the message never said which one.
                        "catch(err){__e=String(err&&err.message||err)+' [at '+location.href+']';}"
                        "return JSON.stringify({e:__e,v:__v});})()"
                        % _thumb_js(it, e.get("project") or "", origin))

                async def attempt(body=body):
                    await live.raw("__forge.clear()")
                    try:
                        got = json.loads(await live.raw(body) or "{}")
                    except Exception as ex:
                        got = {"e": str(ex)[:200]}
                    return got, got.get("e") or ""
                # A game tab opened before its import map fails every builder the same way; the
                # first failure heals the tab and the rest of the shelf builds in the healed one.
                ran = await _healing(live, e, opts, attempt, heal)
                if ran.get("e"):
                    # FIVE, LIKE THE OTHER ONE. This path stayed at four when the success path
                    # grew a `built` field, so the very first asset that failed took the whole
                    # batch down with a ValueError and the endpoint answered 500. A tuple whose
                    # length is the contract between two lines forty apart.
                    shots.append((it, f, "", ran["e"], {}))
                    continue
                img = await live.raw("__forge.view(%s,1.12)" % json.dumps(_THUMB_VIEW))
                built = {}
                try:
                    built = json.loads(ran.get("v") or "{}")
                except (TypeError, ValueError):
                    built = {}
                shots.append((it, f, img, "", built))
            return {"shots": shots, "heal": _heal_report(heal)}
        finally:
            await ws.close()

    try:
        return _run(go)
    except Exception as ex:
        return {"error": str(ex).strip()[:400], "shots": []}


# ---------------------------------------------------------------------------
# An asset that came from somewhere else, and two assets against each other
#
# Every A/B until now was composed by hand: an agent rendered its own asset, someone else rendered
# theirs in another tool with another camera and another rig, and the comparison was two different
# pictures side by side. One camera, one rig, one reference, both scored — that is the difference
# between a comparison and an opinion.
# ---------------------------------------------------------------------------
def _self_base() -> str:
    """This Studio's own origin. Never a hard-coded 8777: a second backend is a different Studio,
    and the page would fetch that one's files."""
    h = str(settings.get("host") or "127.0.0.1")
    if h in ("0.0.0.0", "::"):
        h = "127.0.0.1"
    # ASSET_STUDIO_PORT first, as main.py serves on it: a second backend shares the first one's
    # settings, so settings["port"] named :8777 on :8791 — another Studio, with its own files.
    return "http://%s:%s" % (h, os.environ.get("ASSET_STUDIO_PORT") or settings.get("port") or 8777)


def _sidecar_url(project) -> str:
    """Where the page reads the project's saved edits: the one file, served by this Studio.

    Not /api/workspace/file, which answers 403 for any folder the Studio does not have open — the
    proof game on the Desktop, say — and then the page could only read the file from the game's own
    server, which has nothing when the file sits above the folder it serves (rot-rush serves
    rot-rush/, and its studio.edits.json is in the workspace root)."""
    return "%s/api/live/edits/file?project=%s" % (_self_base(), quote(str(project)))


def _model_url(path: str) -> str:
    """A model file on disk, as a URL the page can fetch — decompressed on the way out."""
    return "%s/api/engine/model?project=&path=%s" % (_self_base(), quote(str(path)))


def _loader_url(project: str, name: str = "GLTFLoader.js") -> str:
    return "%s/api/engine/loader?project=%s&name=%s" % (_self_base(), quote(str(project)), name)


def _forge_open(project: str):
    """(the session entry, the URL to open or "") — how every forge call starts."""
    e = _entry(project)
    want_url = ""
    if _origin_gone(e):
        e["origin_restarted"] = e.get("url")
        e["url"] = ""
    if not e.get("url"):
        origin, how = origin_for(Path(project), start_dev=True)
        want_url = origin.rstrip("/") + "/"
        if how:
            e["how"] = how
    return e, want_url


@_settles
def glb(project: str, path: str = "", name: str = "", clear: bool = True, width: int = 0,
        height: int = 0, background: str = "", engine: str = "", ortho=None,
        light: str = "", env=None, ambient=None, exposure=None,
        lights: Optional[dict] = None, shadows=None) -> dict:
    """Put a GLB on the bench, so `look` can photograph it and the detail review can judge it."""
    bad = _guard(project, forge=True)
    if bad:
        return bad
    p = Path(str(path))
    if not p.is_file():
        return {"ok": False, "error": "no such file: %s" % path}
    try:
        e, want_url = _forge_open(project)
    except Exception as ex:
        return {"ok": False, "error": "could not find a way to serve the project: %s" % ex}
    width, height, background = _canvas(e, width, height, background)
    look_now = _look_state(e, _studio_opts(ortho, light, env, ambient, exposure, lights, shadows))
    opts = json.dumps(dict({"width": int(width), "height": int(height), "background": background,
                            "ground": False, "engine": engine or "", "sky": False}, **look_now))
    url, loader = _model_url(str(p.resolve())), _loader_url(project)
    _doing(project, "loading", p.name, name or p.stem)

    async def go():
        ws, live = await _session(e, want_url=want_url, wait_ms=1500 if want_url else 0)
        try:
            if want_url:
                await _bridge(live, e)
                e["url"] = want_url
            await _forge_script(live)
            built = await live.ask("__forge.ensure(%s)" % opts)
            if not (built or {}).get("ok"):
                return {"built": built}
            if clear:
                await live.raw("__forge.clear()")
            got = await live.ask("__forge.glb(%s,%s,%s)" % (json.dumps(url),
                                                            json.dumps(name or p.stem),
                                                            json.dumps(loader)), depth=4)
            # ONE FRAME FIRST. The draw counters are the last frame's, and nothing had been drawn
            # since the file loaded: the answer said 0 draw calls for a model on the bench.
            await live.raw("(__forge.shot(), 0)")
            stats = await live.ask("__forge.stats()", depth=8) or {}
            return {"built": built, "glb": got, "stats": stats,
                    "symmetry": (await live.ask("__forge.symmetry()", depth=5) or {}) if _checks_on() else {},
                    "at": await live.ask("__forge.at()", depth=4) or {}}
        finally:
            await ws.close()

    try:
        out = _run(go)
    except Exception as ex:
        return {"ok": False, "error": str(ex).strip()[:600]}
    built = out.get("built") or {}
    if not built.get("ok"):
        return {"ok": False, "error": built.get("error") or "the forge could not start"}
    got = out.get("glb") or {}
    if not got.get("ok"):
        return {"ok": False, "error": got.get("error") or "the file did not load"}
    stats = out.get("stats") or {}
    _bench_put(project, "/* GLB: %s */" % p.name, clear, out.get("at") or {}, stats, p.stem,
               model=str(p.resolve()))
    _done(project, "loaded", p.name)
    return {"ok": True, "name": got.get("name"), "engine": built.get("engine"), "stats": stats,
            "file": str(p.resolve()),
            # From boxes alone, before a single picture: a buckle off the midline reads wrong
            # from the front whatever angle the next look is taken from.
            "findings": _symmetry_finding(out.get("symmetry")),
            "next": "photograph it with /api/live/look — add \"detail\":true for the close-ups"}


@_settles
def export_glb(project: str, path: str = "") -> dict:
    """Write what the bench holds to a .glb file."""
    bad = _guard(project, forge=True)
    if bad:
        return bad
    if not str(path).strip():
        return {"ok": False, "error": "where to write it: pass path=<something>.glb"}
    out = Path(str(path))
    e = _entry(project, create=False)
    if not e or not e.get("target"):
        return {"ok": False, "error": "nothing is open for this project. Build it once with "
                                      "/api/live/forge, then export it."}
    exporter = _loader_url(project, "GLTFExporter.js")
    _doing(project, "exporting", out.name)

    async def go():
        ws, live = await _session(e)
        try:
            if not await live.raw("!!window.__forge"):
                return {"gone": "the page has been reloaded since it was built"}
            await _forge_script(live)
            b64 = await live.raw("(async()=>{const r=await __forge.exportGlb(%s);"
                                 "return r&&r.ok?r.b64:'ERR:'+((r&&r.error)||'no answer');})()"
                                 % json.dumps(exporter))
            stats = await live.ask("__forge.stats()", depth=8) or {}
            return {"b64": b64, "stats": stats}
        finally:
            await ws.close()

    try:
        got = _run(go)
    except Exception as ex:
        return {"ok": False, "error": str(ex).strip()[:600]}
    if got.get("gone"):
        return {"ok": False, "error": got["gone"]}
    b64 = str(got.get("b64") or "")
    if not b64 or b64.startswith("ERR:"):
        return {"ok": False, "error": b64[4:] or "the exporter returned nothing"}
    try:
        raw = base64.b64decode(b64)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".part")
        tmp.write_bytes(raw)
        os.replace(tmp, out)
    except Exception as ex:
        return {"ok": False, "error": "could not write it: %s" % str(ex)[:200]}
    _done(project, "exported", out.name)
    return {"ok": True, "path": str(out.resolve()), "bytes": len(raw),
            "stats": got.get("stats") or {}}


@_settles
def compare(project: str, a: str = "", b: str = "", labels: Optional[list] = None,
            views: Optional[list] = None, ref: str = "", width: int = 0, height: int = 0,
            background: str = "", engine: str = "", margin: float = 0.0,
            label: str = "", quality: str = "", tags: Optional[list] = None,
            ortho=None, light: str = "", env=None, ambient=None, exposure=None,
            lights: Optional[dict] = None, shadows=None, ref_mask: str = "", ref_crop: str = "",
            focus: str = "") -> dict:
    """Two assets, one camera, one reference, both scored.

    `focus` names a part: the camera frames that part in both assets at once (the union of what
    matches in each), so a close-up of the same place is one camera too. A close-up is not scored
    against the reference - a part's silhouette is not the whole picture's.

    `a` and `b` are .glb files; leave `a` empty to compare what is already on the bench against a
    file. The sheet is REFERENCE first, then each asset at each angle, and the answer carries both
    silhouette scores — so "theirs is better" becomes a number instead of an impression."""
    bad = _guard(project, forge=True)
    if bad:
        return bad
    if not str(b).strip():
        return {"ok": False, "error": "pass b=<file.glb>; a is the bench when left out"}
    pb = Path(str(b))
    if not pb.is_file():
        return {"ok": False, "error": "no such file: %s" % b}
    pa = Path(str(a)) if str(a).strip() else None
    if pa is not None and not pa.is_file():
        return {"ok": False, "error": "no such file: %s" % a}
    if ref_mask or ref_crop:
        reference(project, ref, ref_mask, ref_crop)
        ref = ""
    got_ref = reference(project, ref) if ref else reference(project)
    ref_path = got_ref.get("reference") or ""
    try:
        e, want_url = _forge_open(project)
    except Exception as ex:
        return {"ok": False, "error": "could not find a way to serve the project: %s" % ex}
    margin = float(margin or _engine_pref("forge_margin") or 1.06)
    want = [w for w in (_view_ok(v) for v in (views or ["front"])) if w][:3] or ["front"]
    names = [str(x) for x in (labels or [])][:2]
    focus_q = str(focus or "").strip()
    la = names[0] if len(names) > 0 else (pa.stem if pa is not None else "bench")
    lb = names[1] if len(names) > 1 else pb.stem
    width, height, background = _canvas(e, width, height, background)
    look_now = _look_state(e, _studio_opts(ortho, light, env, ambient, exposure, lights, shadows))
    opts = json.dumps(dict({"width": int(width), "height": int(height), "background": background,
                            "ground": False, "engine": engine or "", "sky": False}, **look_now))
    loader = _loader_url(project)
    _doing(project, "comparing", "%s against %s" % (la, lb), label or "")

    async def go():
        ws, live = await _session(e, want_url=want_url, wait_ms=1500 if want_url else 0)
        try:
            if want_url:
                await _bridge(live, e)
                e["url"] = want_url
            await _forge_script(live)
            built = await live.ask("__forge.ensure(%s)" % opts)
            if not (built or {}).get("ok"):
                return {"built": built}
            if pa is not None:
                await live.raw("__forge.clear()")
                ga = await live.ask("__forge.glb(%s,%s,%s)"
                                    % (json.dumps(_model_url(str(pa.resolve()))),
                                       json.dumps("__A"), json.dumps(loader)), depth=4)
                if not (ga or {}).get("ok"):
                    return {"built": built, "bad": ga, "which": la}
            gb = await live.ask("__forge.glb(%s,%s,%s)"
                                % (json.dumps(_model_url(str(pb.resolve()))),
                                   json.dumps("__B"), json.dumps(loader)), depth=4)
            if not (gb or {}).get("ok"):
                return {"built": built, "bad": gb, "which": lb}
            shots, sils, no_part = [], [], False
            for v in want:
                # ONE CAMERA FOR BOTH. Framed on everything in the scene, then each subject is
                # hidden in turn — so the two panels are the same picture with one thing changed.
                # EXACT names: a GLB root is a Group, and the part search sees meshes only, so
                # '__B' once matched nothing, hid nothing, and both panels showed both assets.
                close = False
                if focus_q:
                    await live.raw("__forge.view(%s,%s,0,%s)"
                                   % (json.dumps(v), float(margin), json.dumps(focus_q)))
                    at = await live.ask("__forge.at()", depth=3) or {}
                    close = (at or {}).get("framed") == "focus"
                    no_part = no_part or not close
                if not close:
                    await live.raw("__forge.view(%s,%s)" % (json.dumps(v), float(margin)))
                tag = "%s · %s" % (v, focus_q) if close else v
                for who, expr in ((la, "__forge.solo('__B',true,true)"), (lb, "__forge.solo('__B',false,true)")):
                    await live.ask(expr, depth=2)
                    shots.append(("%s · %s" % (who, tag), await live.raw("__forge.shot()")))
                if close:
                    await live.ask("__forge.solo(null)", depth=2)
                    continue
                await live.raw("__forge.setPass('silhouette')")
                for who, expr in ((la, "__forge.solo('__B',true,true)"), (lb, "__forge.solo('__B',false,true)")):
                    await live.ask(expr, depth=2)
                    sils.append(((who, v), await live.raw("__forge.shot()")))
                await live.raw("__forge.clearPass()")
                await live.ask("__forge.solo(null)", depth=2)
            stats = await live.ask("__forge.stats()", depth=8) or {}
            return {"built": built, "shots": shots, "sils": sils, "stats": stats, "no_part": no_part}
        finally:
            await ws.close()

    try:
        out = _run(go)
    except Exception as ex:
        return {"ok": False, "error": str(ex).strip()[:600]}
    built = out.get("built") or {}
    if not built.get("ok"):
        return {"ok": False, "error": built.get("error") or "the forge could not start"}
    if out.get("bad"):
        return {"ok": False, "error": "%s did not load: %s"
                % (out.get("which"), (out["bad"] or {}).get("error") or "?")}
    res: dict = {"ok": True, "engine": built.get("engine"), "a": la, "b": lb,
                 "look": built.get("look") or {}, "scores": {}}
    # THE NUMBERS FIRST. Whoever is reading this wants to know which is closer, not to squint.
    lines = []
    if ref_path:
        msk = got_ref.get("mask") or ""
        for (who, v), data in out.get("sils") or []:
            frame = _decode_shot(data)
            if frame is None:
                continue
            cmp = _score_against(ref_path, frame, v, drawn=True, ref_mask=msk)
            if cmp:
                res["scores"].setdefault(who, {})[v] = cmp
        for v in want:
            pair = [(who, (res["scores"].get(who) or {}).get(v, {}).get("overlap"))
                    for who in (la, lb)]
            if all(p[1] is not None for p in pair):
                better = max(pair, key=lambda p: p[1])
                lines.append("AT %s: %s %.3f, %s %.3f — %s is closer to the reference by %.3f."
                             % (v, pair[0][0], pair[0][1], pair[1][0], pair[1][1], better[0],
                                abs(pair[0][1] - pair[1][1])))
    else:
        lines.append("NO REFERENCE: pass ref=<image path> once and both sides get a score.")
    if focus_q:
        lines.insert(0, ("FOCUS: no part named %r in either asset - drew the whole view." % focus_q)
                     if out.get("no_part") else
                     "FOCUS: %r framed in both assets with one camera; a close-up is not scored "
                     "against the reference." % focus_q)
    res["findings"] = lines

    frames, panels = [], []
    if ref_path:
        rf, _err = _ref_frame(project, "", (int(width), int(height)))
        if rf is not None:
            frames.append(rf)
            panels.append("REFERENCE")
    for name, data in out.get("shots") or []:
        im = _decode_shot(data)
        if im is not None:
            frames.append(im)
            panels.append(name)
    if frames:
        from . import review as _review
        want_q = str(quality or _engine_pref("forge_quality")
                     or settings.get("cc_review_quality") or "normal").lower()
        budget = _review.QUALITY.get(want_q, _review.QUALITY["normal"])[0]
        sheet = _review._sheet(frames, panels, label or ("%s against %s" % (la, lb)),
                               "one camera, one rig, %s" % (built.get("engine") or "?"), budget)
        dst = _LIVE_DIR / _slug(project) / ("compare-%d.png" % int(time.time() * 1000))
        dst.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(dst)
        res["sheet"] = str(dst)
        res["views"] = panels
        try:
            from . import engine as _engine
            gid = _engine.record(project, label or ("%s vs %s" % (la, lb)), "",
                                 built.get("engine", ""), out.get("stats") or {}, panels,
                                 str(dst), True, "", built.get("engine_url", ""),
                                 tags=tags or ["compare"], category="",
                                 extra={"compare": {"a": la, "b": lb, "scores": res["scores"]}})
            if gid:
                res["generation"] = gid
        except Exception:
            pass
    _done(project, "compared", "%s against %s" % (la, lb))
    return res


def forge_clear(project: str, dispose: bool = False) -> dict:
    """Empty the subject, or tear the whole studio down and give the memory back."""
    bad = _guard(project, forge=True)
    if bad:
        return bad
    e = _entry(project)

    async def go():
        ws, live = await _session(e)
        try:
            if not await live.raw("!!window.__forge"):
                return {"built": False}
            return await live.ask("__forge.%s()" % ("dispose" if dispose else "clear"))
        finally:
            await ws.close()

    try:
        done = bool(_run(go))
    except Exception as ex:
        return {"ok": False, "error": str(ex)}
    if done:
        # The page is empty now, so the bench is too. Kept, it went on showing the Engine window
        # the old scene, and the next `clear:false` build was drawn there on top of parts the page
        # no longer had.
        _bench_put(project, "", True, {}, {}, "")
    return {"ok": True, "disposed" if dispose else "cleared": done}


def close(project: str = "") -> dict:
    """Close one project's tab, or every one. The browser itself is not touched."""
    keys = [key_for(project)] if project else list(_tabs)
    targets = []
    with _lock:
        for k in keys:
            e = _tabs.pop(k, None)
            if e and e.get("target"):
                targets.append(e["target"])
    if not targets:
        return {"ok": True, "closed": 0}

    async def go():
        from . import review
        ws = await _connect(review._ensure_browser())
        try:
            cdp = review._Cdp(ws)
            n = 0
            for t in targets:
                try:
                    await cdp.call("Target.closeTarget", {"targetId": t})
                    n += 1
                except Exception:
                    pass
            return n
        finally:
            await ws.close()

    try:
        return {"ok": True, "closed": _run(go)}
    except Exception as ex:
        return {"ok": False, "error": str(ex), "closed": 0, "forgotten": len(targets)}


def status() -> dict:
    ok, why = available()
    with _lock:
        tabs = [{"project": e.get("project", ""), "url": e.get("url", ""),
                 "engine": e.get("engine", ""), "device": e.get("device", "desktop"),
                 "served_by": e.get("how", ""),
                 "age_s": round(time.time() - (e.get("opened") or time.time()), 1),
                 "idle_s": round(time.time() - (e.get("seen") or time.time()), 1)}
                for e in _tabs.values()]
    return {"ok": True, "enabled": enabled(), "available": ok, "why": "" if ok else why,
            "tabs": tabs}


# ---------------------------------------------------------------------------
# Tabs no agent uses any more
# ---------------------------------------------------------------------------
# A tab is opened by an agent's first call and kept on purpose: going back to a live page keeps the
# game exactly as the agent left it. But nothing ever closed one. The browser closes after ten idle
# minutes only when ALL of it is idle, so one agent at work kept every finished agent's game running
# behind it: four PlayCanvas games were found open at once, two of them from agents that had
# stopped 26 and 38 hours earlier, each still rendering, and the Engine window named every one of
# them as "in use". A tab now closes on the browser's own rule, but on its own clock.
_TAB_IDLE_MIN = 10.0
# With no browser running, the tabs died with it and only their records are left. A record this
# fresh belongs to a call that is starting the browser right now, so it is left for that call.
_NO_BROWSER_GRACE_S = 60.0


def _tab_idle_limit() -> float:
    """Seconds with no agent call before a tab is closed (Settings → Studio engine)."""
    try:
        m = float(_engine_pref("live_tab_idle_min", _TAB_IDLE_MIN) or 0)
    except (TypeError, ValueError):
        m = 0.0
    return (m if m > 0 else _TAB_IDLE_MIN) * 60.0


def _idle_tab_keys(tabs: dict, now: float, limit_s: float, keep: set) -> list:
    """The tabs to close: no agent call for `limit_s`, and not in `keep`.

    `seen` moves on every agent call and never for someone watching (see live_stream), so a person
    looking at a quiet game is not an agent using it. That person's tab is in `keep` instead,
    because closing it would pull the picture away from under them; so is a tab a call is working
    in this second."""
    out = []
    for k, e in tabs.items():
        if k in keep:
            continue
        seen = float(e.get("seen") or e.get("opened") or 0)
        if now - seen > limit_s:
            out.append(k)
    return out


def _running_browser_ws() -> str:
    """The shared browser's endpoint while it runs, else "". Never starts it and never counts as a
    use of it: `review._ensure_browser` does both, and a reaper that called it would keep alive the
    very browser it is meant to let go."""
    try:
        from . import review
        proc = review._state.get("proc")
        if proc is not None and proc.poll() is None:
            return str(review._state.get("ws") or "")
    except Exception:
        pass
    return ""


def reap_idle_tabs(now: Optional[float] = None) -> dict:
    """Close every tab no agent has called for the idle limit. The browser's reaper calls this.

    An agent that comes back later still gets its answer: `_session` opens a fresh tab for it."""
    now = time.time() if now is None else now
    keep: set = set()
    try:
        from . import live_stream
        keep |= set(live_stream.watched_keys())
    except Exception:
        pass
    for row in list(_ACTIVITY.values()):          # a call is working in this tab right now
        if not row.get("ended") and now - float(row.get("since") or now) < _ACTIVITY_STALE:
            keep.add(key_for(str(row.get("project") or "")))
    ws_url = _running_browser_ws()
    limit = _tab_idle_limit() if ws_url else _NO_BROWSER_GRACE_S
    with _lock:
        keys = _idle_tab_keys(_tabs, now, limit, keep)
        gone = [e for e in (_tabs.pop(k, None) for k in keys) if e is not None]
    if not gone:
        return {"closed": 0, "forgotten": 0, "projects": []}
    targets = [e["target"] for e in gone if e.get("target")]
    closed = 0
    if ws_url and targets:
        async def go():
            from . import review
            ws = await _connect(ws_url)
            try:
                cdp = review._Cdp(ws)
                n = 0
                for t in targets:
                    try:
                        await cdp.call("Target.closeTarget", {"targetId": t})
                        n += 1
                    except Exception:
                        pass
                return n
            finally:
                await ws.close()

        try:
            closed = int(_run(go) or 0)
        except Exception:
            closed = 0
    return {"closed": closed, "forgotten": len(gone) - closed,
            "projects": [str(e.get("project") or "") for e in gone]}

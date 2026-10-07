# -*- coding: utf-8 -*-
"""The agent's own tab, as a live picture a person can watch while the agent works in it.

The Engine window's Game tab plays a SECOND copy of the game in an iframe. The agent's moves —
`/api/live/edit`, `/place`, `/eval`, a forge run — happen in a different place: the headless tab
the live link keeps open. So a person watching the Game tab watched a game nobody was changing.
This streams THAT tab, the one the agent's calls use, as MJPEG an `<img>` can play:

    GET /api/live/stream?project=<abs>&fps=8&quality=70&max_w=1280   -> multipart/x-mixed-replace
    GET /api/live/stream/status?project=<abs>                        -> {live, streaming, fps_measured, ...}

THREE RULES, and each one is a promise to somebody.

  1. WATCHING NEVER CHANGES THE GAME. The stream never opens, navigates, reloads, resizes or
     evaluates anything in the tab. It attaches its own debugger session and asks for frames, and
     that is all. No tab -> 404, and the person is told the view starts when an agent opens one.
     It does not even touch the tab record's `seen` clock (live._entry does, on every call), so
     "an agent was here in the last 20 s" stays a fact about agents, not about viewers.

  2. ONE SCREENCAST PER TAB, however many people watch. Every viewer shares it (a refcount); it
     stops 10 s after the last one leaves, when the tab goes away, or after 10 minutes in which no
     agent touched the tab. Its own thread and its own event loop, so nothing here ever blocks the
     backend's loop; each HTTP viewer is a plain generator in the threadpool waiting on a
     threading.Condition.

  3. IT MUST NOT COST THE AGENT ITS FRAME RATE. Measured on this machine, on the Arena Proof game:
     the headless browser runs its compositor unthrottled (--disable-gpu-vsync), so a screencast is
     offered ~100 frames a second. Acking each one the moment it arrived cost the browser +65% of a
     core and put a 15.7 ms hitch into the game's frames. So the acks are PACED: every frame is
     acked, but one per 1/fps, and Chrome only captures and encodes a new frame when a slot frees
     (it keeps at most three in flight). Same frames a viewer can use, +17% of a core, no hitch.

The price of pacing is the LAST frame. A page that paints once and then goes still may have
painted while every slot was full, and then nothing else ever frees one with something to show.
So when the stream has been quiet for a moment after an ack, the screencast is restarted once —
a restart always delivers one frame of the page as it is now — and the picture is right again.

And a page that renders the same picture sixty times a second sends the same bytes each time; a
frame identical to the last one is acked and not sent again, so a still scene costs a viewer
nothing and the measured rate reads ~0, which is the truth about what changed.
"""
from __future__ import annotations

import asyncio
import base64
import collections
import itertools
import json
import os
import threading
import time
from typing import Any, Iterator, Optional

from . import live

MEDIA_TYPE = "multipart/x-mixed-replace; boundary=frame"
HEADERS = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache",
           "X-Accel-Buffering": "no"}
NO_TAB = "no agent has this game open — the view starts when one opens it"
LIVE_OFF = "Live game link is off. Turn it on in Settings → Planning."

# What a request may ask for, and what the settings give when it asks for nothing. A setting out of
# range is clamped the same way a query is: a typo in Settings must not break every viewer.
FPS_RANGE = (1, 30)
QUALITY_RANGE = (30, 95)
WIDTH_RANGE = (320, 1920)
DEFAULTS = {"fps": 8, "quality": 70, "max_w": 1280}

_LINGER_S = 10.0           # the screencast outlives its last viewer by this much (a tab switch back)
_IDLE_S = 600.0            # no agent call on the tab for this long -> stop; a click resumes it
_KEEPALIVE_S = 5.0         # a still picture is re-sent this often, so a dead connection shows
_TICK_S = 1.0              # the longest a viewer's generator waits before handing control back
_SETTLE_S = 0.35           # quiet this long after an ack -> one refresh frame (see the docstring)
_VIEWER_STALE_S = 30.0     # a viewer that has not pulled for this long is gone, whatever it says
_MAX_VIEWERS = 6           # each viewer holds a threadpool worker while it waits; the pool is shared
_AGENT_ACTIVE_S = 20.0     # "an agent is working" means a call touched the tab this recently
_CONTROL_S = 0.25          # how often the cast looks after itself
_TARGETS_TTL = 1.0         # one /json/list a second, however many status polls arrive
_REPLACE_GRACE_S = 5.0     # a record with no tab this long means closed; shorter, a tab being replaced

_reg = threading.Lock()                  # guards _casts, _last_stop and every cast's viewers
_casts: dict = {}                        # tab key -> _Cast
_last_stop: dict = {}                    # tab key -> {"why", "at"}: what the status says after
_targets_cache: dict = {}                # browser port -> (monotonic, {target id: info})
_ids = itertools.count(1)
# FRAME TIMING RUNS ON perf_counter. On Windows time.monotonic() is GetTickCount64 and moves in
# 15.6 ms steps, which turns a 33 ms frame gap into 31 or 47 and caps a 30 fps stream near 21.
_clock = time.perf_counter


# ---------------------------------------------------------------------------
# Small pure pieces (unit-tested)
# ---------------------------------------------------------------------------
def _clamp(v, lo: int, hi: int, default: int) -> int:
    try:
        n = int(round(float(v)))
    except Exception:
        return default
    if n <= 0:
        return default
    return max(lo, min(hi, n))


def params(fps: Any = 0, quality: Any = 0, max_w: Any = 0) -> dict:
    """The picture to ask for: the query where it says something, else Settings → Studio engine."""
    d_fps = _clamp(live._engine_pref("live_watch_fps", DEFAULTS["fps"]), *FPS_RANGE, DEFAULTS["fps"])
    d_q = _clamp(live._engine_pref("live_watch_quality", DEFAULTS["quality"]), *QUALITY_RANGE,
                 DEFAULTS["quality"])
    d_w = _clamp(live._engine_pref("live_watch_width", DEFAULTS["max_w"]), *WIDTH_RANGE,
                 DEFAULTS["max_w"])
    return {"fps": _clamp(fps, *FPS_RANGE, d_fps),
            "quality": _clamp(quality, *QUALITY_RANGE, d_q),
            "max_w": _clamp(max_w, *WIDTH_RANGE, d_w)}


# THE HEADERS OF THE NEXT PART GO OUT WITH EACH FRAME, and that is the whole trick.
#
# Chrome hands a part's pixels to the <img> only when it has parsed the NEXT part's header block
# (ImageResource::OnePartInMultipartReceived). Two framings failed on the Game tab before this one:
# the usual one, boundary first (`--frame, headers, JPEG`), showed every frame one frame late —
# on a still scene the keepalive five seconds later, so an agent's one edit reached the person
# watching five seconds late; and closing each part with the boundary alone did no better, because
# the headers after it were still missing. Measured in a real tab both times: the frame had
# arrived (14,901 bytes) and the image still sat at naturalWidth 0.
#
# So each part is sent as the JPEG, then the boundary, then the next part's headers, and a frame
# shows the moment it lands. The price: no Content-Length — the next part's headers are written
# before its JPEG exists. A reader splits on the boundary, as Chrome itself does.
PART_HEAD = b"Content-Type: image/jpeg\r\n\r\n"
DELIMITER = b"\r\n--frame\r\n"
OPENING = b"--frame\r\n" + PART_HEAD


def part(jpeg: bytes) -> bytes:
    """One frame: its JPEG, closed at once by the boundary and the next part's headers. The stream
    starts with OPENING, once, so every part has its headers before its bytes."""
    return jpeg + DELIMITER + PART_HEAD


def jpeg_size(b: bytes) -> Optional[tuple]:
    """(width, height) from the frame header, without decoding the picture."""
    if len(b) < 4 or b[:2] != b"\xff\xd8":
        return None
    i, n = 2, len(b)
    while i + 9 < n:
        if b[i] != 0xFF:
            i += 1
            continue
        m = b[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        seg = int.from_bytes(b[i + 2:i + 4], "big")
        if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h = int.from_bytes(b[i + 5:i + 7], "big")
            w = int.from_bytes(b[i + 7:i + 9], "big")
            return (w, h)
        i += 2 + seg
    return None


# ---------------------------------------------------------------------------
# Finding the tab, without touching it
# ---------------------------------------------------------------------------
def _tab(key: str) -> Optional[dict]:
    """The tab record for one key, copied. NOT live._entry: that stamps `seen` on every call, and a
    status poll every three seconds would make every tab look busy with an agent forever."""
    with live._lock:
        e = live._tabs.get(key)
        return dict(e) if e is not None else None


def _too_broad(path: str) -> bool:
    """A drive root or the home folder: the same rule the graph and the dev-server finder use."""
    try:
        from .graphify_index import absurd_root
        return bool(absurd_root(path))
    except Exception:                                              # noqa: BLE001
        try:
            from pathlib import Path
            p = Path(path)
            return p.parent == p
        except Exception:                                          # noqa: BLE001
            return False


def _peek(project: str) -> tuple:
    """(key, tab record copy, how it matched) — or ("", None, "").

    Exact first — a record WITH a tab. Then a tab opened on a folder INSIDE this one, or on the
    folder that contains it: the Engine window names a game by its workspace, and an agent may have
    opened the game's own sub-folder (rot-rush inside the brainrot workspace), or the other way round.

    An exact record with no tab is not an answer: live._entry makes one for any call on the path,
    a refused one too, and taking it hid a live tab on the sub-folder behind "no agent has this game
    open". When several qualify, the CLOSEST folder wins, then the tab opened last — never the one
    SEEN last: two agents in two sub-games take turns being seen, and the status and the stream
    each resolved to whichever had just been touched, so the picture kept switching games.
    """
    if not str(project or "").strip():
        return "", None, ""          # Path("").resolve() is the backend's own folder: never a game
    want = live.key_for(project)
    with live._lock:
        e = live._tabs.get(want)
        if e is not None and e.get("target"):
            return want, dict(e), "exact"
        exact = dict(e) if e is not None else None
        # Copied under the lock, matched outside it: _too_broad resolves paths on disk, and every
        # live call in the backend takes this lock.
        items = [(k, dict(t)) for k, t in live._tabs.items() if t.get("target") and k != want]
    depth = lambda p: len([s for s in p.replace("\\", "/").split("/") if s])
    best = None
    for k, t in items:
        if k.startswith(want.rstrip("\\/") + os.sep) or k.startswith(want.rstrip("\\/") + "/"):
            how = "inside"
            outer = want
        elif want.startswith(k.rstrip("\\/") + os.sep) or want.startswith(k.rstrip("\\/") + "/"):
            how = "contains"
            outer = k
        else:
            continue
        # A drive root or the home folder "contains" every game on the machine; a relation
        # through one of those is a coincidence, not the same game.
        if _too_broad(outer):
            continue
        rank = (-abs(depth(k) - depth(want)), float(t.get("opened") or 0), k)
        if best is None or rank > best[3]:
            best = (k, t, how, rank)
    if best:
        return best[0], best[1], best[2]
    if exact is not None:
        return want, exact, "exact"
    return "", None, ""


def _browser() -> tuple:
    """(debugger ws url, port) of the browser the live tabs live in — or ("", 0).

    Read, never launched: review._ensure_browser() would start a Chrome to show a tab that cannot
    exist in it, and it stamps the browser's idle clock, so a person watching would keep a browser
    alive that the reaper exists to close. When nobody works, the browser goes and the view with it.
    """
    try:
        from . import review
        st = review._state
        proc = st.get("proc")
        if proc is None or proc.poll() is not None or not st.get("ws"):
            return "", 0
        return str(st["ws"]), int(st.get("port") or 0)
    except Exception:
        return "", 0


def _alive_targets(port: int) -> dict:
    """{target id: info} for the pages the browser has now. The debugger's own HTTP list: no socket
    to a page, nothing a page could notice. Cached for a second."""
    if not port:
        return {}
    now = time.monotonic()
    hit = _targets_cache.get(port)
    if hit and now - hit[0] < _TARGETS_TTL:
        return hit[1]
    got: dict = {}
    try:
        import httpx
        for t in httpx.get("http://127.0.0.1:%d/json/list" % port, timeout=1.5).json():
            if t.get("type") == "page" and t.get("id"):
                got[t["id"]] = {"url": t.get("url") or "", "title": t.get("title") or ""}
    except Exception:
        got = {}
    _targets_cache[port] = (now, got)
    return got


# ---------------------------------------------------------------------------
# One screencast per tab
# ---------------------------------------------------------------------------
class _Viewer:
    def __init__(self, fps: int):
        self.id = next(_ids)
        self.fps = fps
        self.cast: Optional["_Cast"] = None
        self.opened = time.time()
        self.last_pull = time.monotonic()
        self.sent = 0
        self.gone = False


class _Cast:
    """The screencast of one tab, on its own thread and event loop."""

    def __init__(self, key: str, project: str, target: str, ws_url: str, want: dict):
        self.key = key
        self.project = project
        self.target = target
        self.ws_url = ws_url
        self.fps = want["fps"]
        self.quality = want["quality"]
        self.max_w = want["max_w"]
        self.cond = threading.Condition()
        self.frame = b""
        self.seq = 0
        self.frame_at = 0.0            # wall clock of the newest frame a viewer can get
        self.size: Optional[tuple] = None
        self.viewers: dict = {}        # guarded by _reg
        self.empty_since = time.monotonic()
        self.started = time.time()
        self.stopping = False          # decided, under _reg; viewers end at once
        self.stopped = False           # the thread has finished
        self.why = ""
        self.error = ""
        self.visible: Optional[bool] = None
        self.attached = False
        self.received = 0
        self.published = 0
        self.acked = 0
        self.refreshes = 0
        self.switches = 0
        self._recv: collections.deque = collections.deque(maxlen=512)
        self._pub: collections.deque = collections.deque(maxlen=512)
        # Everything below is touched only on the cast's own thread.
        self._ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._sid = ""
        self._n = 0
        self._fut: dict = {}
        self._sends: set = set()
        self._pending: Optional[str] = None     # newest frame not yet handed out (base64)
        self._held: collections.deque = collections.deque()   # frames received, not yet acked
        self._cur_no: Optional[int] = None      # the running screencast's number (Chrome's)
        self._last_tick = 0.0
        self._timer = None
        self._last_b64 = ""
        self._last_frame = 0.0
        self._last_ack = 0.0
        self._settled = True
        self._refresh_pending = False
        self._cast_params: Optional[tuple] = None
        self._gone = ""
        self._no_tab_since: Optional[float] = None
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name="live-stream-%s" % os.path.basename(key)[:24])

    # -- lifecycle ---------------------------------------------------------------------------
    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as ex:                                    # noqa: BLE001
            self.error = self.error or str(ex)[:300] or type(ex).__name__
            self._decide_stop("error: " + self.error)
        finally:
            self._decide_stop(self.why or "stopped")
            with self.cond:
                self.stopped = True
                self.attached = False
                self.cond.notify_all()

    def _decide_stop(self, why: str, only_if_empty: bool = False) -> bool:
        """Stop, once. `only_if_empty` re-checks under the lock, so a viewer who arrives in the same
        instant the last one's linger runs out keeps the stream instead of getting a dead one."""
        with _reg:
            if self.stopping:
                return True
            if only_if_empty and self.viewers:
                return False
            self.stopping = True
            self.why = why
            if _casts.get(self.key) is self:
                _casts.pop(self.key, None)
            _last_stop[self.key] = {"why": why, "at": time.time()}
        with self.cond:
            self.cond.notify_all()
        return True

    async def _main(self) -> None:
        import websockets
        self._loop = asyncio.get_running_loop()
        # No keepalive pings: this socket is local, and a ping the browser never answers would close
        # a healthy stream after twenty seconds.
        async with websockets.connect(self.ws_url, max_size=64 * 1024 * 1024, ping_interval=None,
                                      open_timeout=10, close_timeout=2, compression=None) as ws:
            self._ws = ws
            reader = asyncio.create_task(self._reader())
            try:
                await self._attach(self.target)
                while not self.stopping:
                    await asyncio.sleep(_CONTROL_S)
                    if reader.done():
                        self._decide_stop("the browser closed")
                        break
                    await self._control()
            finally:
                if self._timer is not None:
                    self._timer.cancel()
                    self._timer = None
                await self._detach()
                reader.cancel()

    # -- the debugger connection -------------------------------------------------------------
    async def _call(self, method: str, prm: Optional[dict] = None, sid: str = "",
                    timeout: float = 10.0) -> dict:
        self._n += 1
        i = self._n
        fut = self._loop.create_future()
        self._fut[i] = fut
        msg: dict = {"id": i, "method": method, "params": prm or {}}
        if sid:
            msg["sessionId"] = sid
        try:
            await self._ws.send(json.dumps(msg))
            r = await asyncio.wait_for(fut, timeout)
        finally:
            self._fut.pop(i, None)
        if "error" in r:
            err = r["error"]
            raise RuntimeError("%s: %s" % (method, err.get("message", err) if isinstance(err, dict) else err))
        return r.get("result") or {}

    async def _send(self, msg: str) -> None:
        try:
            await self._ws.send(msg)
        except Exception:                                          # noqa: BLE001
            pass

    async def _reader(self) -> None:
        """Every message from the browser. Replies go to their callers; the events that matter are
        frames, and the tab going away. `review._Cdp.call` skips events, which is why this exists."""
        try:
            async for raw in self._ws:
                try:
                    m = json.loads(raw)
                except Exception:                                  # noqa: BLE001
                    continue
                mid = m.get("id")
                if mid is not None:
                    f = self._fut.pop(mid, None)
                    if f is not None and not f.done():
                        f.set_result(m)
                    continue
                meth = m.get("method") or ""
                p = m.get("params") or {}
                sid = m.get("sessionId") or ""
                if meth == "Page.screencastFrame":
                    if sid and sid == self._sid:
                        self._on_frame(p)
                elif meth == "Target.detachedFromTarget":
                    if self._sid and p.get("sessionId") == self._sid:
                        self._on_gone("the tab was closed")
                elif meth == "Inspector.detached":
                    if self._sid and sid == self._sid:
                        self._on_gone(str(p.get("reason") or "the tab was closed"))
                elif meth == "Inspector.targetCrashed":
                    if sid and sid == self._sid:
                        self.error = "the agent's tab crashed"
                elif meth == "Page.screencastVisibilityChanged":
                    if sid and sid == self._sid:
                        self.visible = bool(p.get("visible"))
        except Exception:                                          # noqa: BLE001
            pass

    async def _attach(self, target: str) -> None:
        r = await self._call("Target.attachToTarget", {"targetId": target, "flatten": True})
        self._sid = str(r.get("sessionId") or "")
        if not self._sid:
            raise RuntimeError("the browser gave no session for the tab")
        self.target = target
        self._gone = ""
        self._held.clear()
        self._pending = None
        self._cur_no = None
        await self._start()
        self.attached = True

    async def _start(self) -> None:
        """Ask for frames. `maxHeight` is the same number as `maxWidth` on purpose: the long edge is
        what costs bytes, and a phone layout at 3x is 1170x2532 with only a width limit."""
        q, w = int(self.quality), int(self.max_w)
        # The first frame after a start is a picture of the page as it is, even a still one. Count
        # it as a refresh, so a page that never paints is not "settled" a second time for nothing.
        self._refresh_pending = True
        self._settled = True
        await self._call("Page.startScreencast", {"format": "jpeg", "quality": q, "maxWidth": w,
                                                  "maxHeight": w, "everyNthFrame": 1}, self._sid)
        self._cast_params = (q, w)

    async def _restart(self) -> None:
        try:
            await self._call("Page.stopScreencast", {}, self._sid, timeout=3.0)
            await self._start()
        except Exception as ex:                                    # noqa: BLE001
            self.error = "restart failed: %s" % str(ex)[:200]

    async def _detach(self) -> None:
        sid, self._sid = self._sid, ""
        self.attached = False
        if not sid:
            return
        for method, prm, s in (("Page.stopScreencast", {}, sid),
                               ("Target.detachFromTarget", {"sessionId": sid}, "")):
            try:
                await self._call(method, prm, s, timeout=2.0)
            except Exception:                                      # noqa: BLE001
                pass

    def _on_gone(self, why: str) -> None:
        self._sid = ""
        self.attached = False
        self._gone = why or "the tab was closed"

    # -- frames: paced acks, the newest frame wins -----------------------------------------
    def _on_frame(self, p: dict) -> None:
        no = p.get("sessionId")
        data = p.get("data") or ""
        if isinstance(no, int):
            if self._cur_no is not None and no < self._cur_no:
                # A straggler from a screencast this cast restarted. Its ack is void in Chrome and
                # it holds no slot of the running one; ack it anyway and move on.
                self._ack(no)
                return
            if self._cur_no is None or no > self._cur_no:
                self._cur_no = no
                self._held.clear()
        now = _clock()
        self.received += 1
        self._recv.append(now)
        self._last_frame = now
        if self._refresh_pending:
            self._refresh_pending = False
        else:
            self._settled = False
        self._held.append(no)
        if data:
            # The NEWEST frame decides what is shown. A frame that is back to what the viewers
            # already have cancels a different one still waiting, or a flicker would be the last word.
            self._pending = None if data == self._last_b64 else data
        self._pump()

    def _pump(self) -> None:
        """At most once per 1/fps: hand out the newest frame, and ack ONE held frame so Chrome may
        capture the next. Holding the rest is what keeps it from encoding frames nobody sees."""
        now = _clock()
        gap = 1.0 / max(1, int(self.fps))
        if now - self._last_tick >= gap - 0.002 and (self._pending is not None or self._held):
            self._last_tick = now
            if self._pending is not None:
                data, self._pending = self._pending, None
                self._publish(data)
            if self._held:
                self._ack(self._held.popleft())
        if (self._pending is not None or self._held) and self._timer is None and self._loop:
            delay = max(0.0, self._last_tick + gap - _clock())
            self._timer = self._loop.call_later(delay, self._on_timer)

    def _on_timer(self) -> None:
        self._timer = None
        self._pump()

    def _ack(self, no) -> None:
        if not self._sid or self._ws is None or not isinstance(no, int):
            return
        self._n += 1
        msg = json.dumps({"id": self._n, "method": "Page.screencastFrameAck",
                          "params": {"sessionId": no}, "sessionId": self._sid})
        self.acked += 1
        self._last_ack = _clock()
        t = self._loop.create_task(self._send(msg))
        self._sends.add(t)
        t.add_done_callback(self._sends.discard)

    def _publish(self, data: str) -> None:
        try:
            jpeg = base64.b64decode(data)
        except Exception:                                          # noqa: BLE001
            return
        self._last_b64 = data
        size = jpeg_size(jpeg) or self.size
        with self.cond:
            self.frame = jpeg
            self.seq += 1
            self.frame_at = time.time()
            self.size = size
            self.cond.notify_all()
        self.published += 1
        self._pub.append(_clock())

    # -- looking after itself --------------------------------------------------------------
    async def _control(self) -> None:
        now = time.monotonic()
        _reap_stale(self)
        with _reg:
            empty = not self.viewers
            empty_since = self.empty_since
        if empty and now - empty_since >= _LINGER_S:
            if self._decide_stop("nobody is watching", only_if_empty=True):
                return
        if not live.enabled():
            self._decide_stop("the live link was switched off")
            return
        e = _tab(self.key)
        if e is None or not e.get("target"):
            # BEING REPLACED, until it has been gone a while. A record with no tab is also what an
            # agent's close-and-open looks like from here, and stopping at once ended the person's
            # stream in the middle of it (measured: the target stayed empty for 1.6 s, then the new
            # one came, and nobody was attached to it). The last frame stays up meanwhile.
            if self._no_tab_since is None:
                self._no_tab_since = now
            if now - self._no_tab_since < _REPLACE_GRACE_S:
                return
            self._decide_stop(self._gone or "the agent's tab was closed")
            return
        self._no_tab_since = None
        if time.time() - max(float(e.get("seen") or 0), self.started) >= _IDLE_S:
            self._decide_stop("idle")
            return
        want = str(e.get("target"))
        if want != self.target:
            # The agent's tab was replaced (an open with reload makes a new one). Follow it: the
            # person asked to watch the agent, not a tab the agent has left.
            await self._detach()
            try:
                await self._attach(want)
                self.switches += 1
            except Exception as ex:                                # noqa: BLE001
                self._decide_stop("could not follow the agent's new tab: %s" % str(ex)[:160])
            return
        if self._gone:
            self._decide_stop(self._gone)
            return
        if not self.attached:
            return
        if (int(self.quality), int(self.max_w)) != self._cast_params:
            await self._restart()
            return
        if (not self._settled and not self._refresh_pending and self._last_ack > self._last_frame
                and _clock() - self._last_ack >= _SETTLE_S):
            # Quiet since the last ack freed a slot: the page has stopped painting. One restart gives
            # one frame of the page as it is, in case its last paint came while every slot was full.
            self._settled = True
            self.refreshes += 1
            await self._restart()

    # -- read by the status route ----------------------------------------------------------
    def rates(self) -> tuple:
        now = _clock()
        span = min(3.0, max(0.5, time.time() - self.started))
        pub = sum(1 for t in list(self._pub) if now - t <= span) / span
        rec = sum(1 for t in list(self._recv) if now - t <= span) / span
        return round(pub, 1), round(rec, 1)


def _reap_stale(c: _Cast) -> None:
    """A viewer whose generator stopped being pulled is gone. The generator's own `finally` is the
    normal way out; this is for the one Starlette abandoned before it ever ran."""
    now = time.monotonic()
    with _reg:
        for vid, v in list(c.viewers.items()):
            if now - v.last_pull > _VIEWER_STALE_S:
                v.gone = True
                c.viewers.pop(vid, None)
                if not c.viewers:
                    c.empty_since = now


# ---------------------------------------------------------------------------
# What the routes call
# ---------------------------------------------------------------------------
def open_viewer(project: str, fps: Any = 0, quality: Any = 0, max_w: Any = 0) -> tuple:
    """(viewer, None, 200) — or (None, {"ok": False, "error": …}, status). Opens nothing in the page."""
    if not live.enabled():
        return None, {"ok": False, "error": LIVE_OFF}, 404
    key, e, _how = _peek(project)
    if e is None or not e.get("target"):
        return None, {"ok": False, "error": NO_TAB}, 404
    ws_url, port = _browser()
    if not ws_url or str(e["target"]) not in _alive_targets(port):
        return None, {"ok": False, "error": NO_TAB}, 404
    want = params(fps, quality, max_w)
    v = _Viewer(want["fps"])
    start = None
    with _reg:
        if sum(len(c.viewers) for c in _casts.values()) >= _MAX_VIEWERS:
            return None, {"ok": False, "error": "%d people are already watching live tabs — close one "
                                                "and try again" % _MAX_VIEWERS}, 429
        c = _casts.get(key)
        if c is None or c.stopping:
            c = _Cast(key, str(e.get("project") or project), str(e["target"]), ws_url, want)
            _casts[key] = c
            start = c
        else:
            # The most demanding viewer sets the picture: nobody's view gets worse because somebody
            # else started watching. A lower fps is served by that viewer's own generator.
            c.fps = max(c.fps, want["fps"])
            c.quality = max(c.quality, want["quality"])
            c.max_w = max(c.max_w, want["max_w"])
        v.cast = c
        c.viewers[v.id] = v
    if start is not None:
        start.thread.start()
    return v, None, 200


def leave(v: _Viewer) -> None:
    with _reg:
        if v.gone:
            return
        v.gone = True
        c = v.cast
        if c is not None and c.viewers.pop(v.id, None) is not None and not c.viewers:
            c.empty_since = time.monotonic()


def watched_keys() -> set:
    """The tabs somebody is watching right now: a viewer on their screencast. The tab reaper leaves
    these open (live.reap_idle_tabs): a person looking at a quiet game is not an agent using it,
    but closing the tab would pull the picture away from under them."""
    with _reg:
        return {k for k, c in _casts.items() if c.viewers}


def jpeg_frames(v: _Viewer) -> Iterator[bytes]:
    """One viewer's frames, as JPEG bytes; b"" is an empty tick. Sync on purpose: each step runs in
    the threadpool, and the wait is a threading.Condition, so the backend's event loop never waits
    on a frame. Both transports step it: `frames` (MJPEG) and the Game tab's WebSocket.

    Every step returns within `_TICK_S` — a frame, a keepalive or an empty tick — because a closed
    connection is only noticed between steps, and the linger clock starts when this ends."""
    c = v.cast
    last_seq = 0
    last_sent = 0.0
    gap = 1.0 / max(1, int(v.fps))
    try:
        while not v.gone and c is not None:
            with c.cond:
                if c.seq == last_seq and not (c.stopping or c.stopped):
                    c.cond.wait(_TICK_S)
                seq, jpeg, done = c.seq, c.frame, (c.stopping or c.stopped)
            v.last_pull = time.monotonic()
            now = _clock()
            if seq != last_seq and jpeg:
                early = last_sent + gap - now
                if early > 0:
                    time.sleep(min(early, _TICK_S))
                    with c.cond:
                        seq, jpeg = c.seq, c.frame        # the newest, after the wait
                last_seq, last_sent = seq, _clock()
                v.sent += 1
                yield jpeg
                continue
            if done:
                return
            if jpeg and now - last_sent >= _KEEPALIVE_S:
                last_sent = now
                yield jpeg
                continue
            yield b""
    finally:
        leave(v)


def frames(v: _Viewer) -> Iterator[bytes]:
    """One viewer's MJPEG body: `jpeg_frames`, each frame framed as a part (see PART_HEAD)."""
    inner = jpeg_frames(v)
    opened = False
    try:
        for jpeg in inner:
            if not jpeg:
                yield b""
                continue
            yield part(jpeg) if opened else OPENING + part(jpeg)
            opened = True
    finally:
        inner.close()                  # its own `finally` lets the viewer go, now and not at collection
        leave(v)                       # and a generator closed before its first step never ran it


def status(project: str) -> dict:
    """What the Game tab shows above the picture. Cheap and read-only: no socket to the page."""
    key, e, how = _peek(project)
    enabled = live.enabled()
    ws_url, port = _browser()
    page: dict = {}
    if enabled and e is not None and e.get("target") and port:
        page = _alive_targets(port).get(str(e["target"])) or {}
    with _reg:
        c = _casts.get(key) if key else None
        viewers = len(c.viewers) if c is not None else 0
        stop = dict(_last_stop.get(key) or {}) if key else {}
    now = time.time()
    seen = float((e or {}).get("seen") or 0)
    out: dict = {
        "ok": True,
        "live": bool(page),
        "url": page.get("url") or (e or {}).get("url") or "",
        "streaming": bool(c is not None and not c.stopping and not c.stopped),
        "viewers": viewers,
        "fps_measured": 0.0,
        "last_frame_ms_ago": None,
        "agent_active": bool(e is not None and seen and now - seen < _AGENT_ACTIVE_S),
        "enabled": enabled,
        "settings": params(),
    }
    if (e or {}).get("engine") and e.get("engine") != "?":
        out["engine"] = e["engine"]
    if e is not None:
        out["project"] = e.get("project") or ""
        out["match"] = how
        out["agent_idle_s"] = round(max(0.0, now - seen), 1) if seen else None
        if page.get("title"):
            out["title"] = page["title"]
    if c is not None:
        pub, rec = c.rates()
        out["fps_measured"] = pub
        out["fps_source"] = rec
        if c.frame_at:
            out["last_frame_ms_ago"] = int(max(0.0, now - c.frame_at) * 1000)
        out["stream"] = {"fps": c.fps, "quality": c.quality, "max_w": c.max_w,
                         "size": list(c.size) if c.size else None, "attached": c.attached,
                         "received": c.received, "published": c.published, "acked": c.acked,
                         "refreshes": c.refreshes, "switches": c.switches,
                         "since_s": round(now - c.started, 1)}
        if c.error:
            out["stream"]["error"] = c.error
    if stop and not out["streaming"]:
        out["stopped"] = {"why": stop.get("why", ""), "ago_s": round(now - float(stop.get("at") or now), 1)}
    if not enabled:
        out["why"] = LIVE_OFF
    elif not out["live"]:
        out["why"] = NO_TAB
    return out


def shutdown(timeout: float = 3.0) -> int:
    """Stop every screencast (tests, and a backend on its way out). Returns how many were running."""
    with _reg:
        cs = list(_casts.values())
    for c in cs:
        c._decide_stop("shutdown")
    end = time.monotonic() + timeout
    for c in cs:
        if c.thread.is_alive():
            c.thread.join(max(0.0, end - time.monotonic()))
    return len(cs)

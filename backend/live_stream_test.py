# -*- coding: utf-8 -*-
"""The agent's tab as a stream a person watches: what it may do, and what it must never do.

No real browser here. A small fake speaks just enough of the DevTools protocol — attach, start and
stop a screencast, frames with Chrome's at-most-three-in-flight rule, a refresh frame on every start,
a tab that closes — and writes down every command it is sent. That list is the first thing under
test: a person watching must not change the game, so the stream may attach, ask for frames, ack
them, stop and detach, and NOTHING else. No createTarget, no navigate, no reload, no evaluate.

The rest is the machinery that is easy to get subtly wrong: the MJPEG framing, the refcount and the
linger, the idle stop, following a replaced tab, the paced acks (the newest frame wins, the last
one of a burst is never lost), and the status a Game tab polls.

    python live_stream_test.py
"""
import asyncio
import base64
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets                                  # noqa: E402

from asset_studio import live, live_stream as ls, review   # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, repr(extra)[:300]))


def wait_for(pred, timeout=3.0, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return bool(pred())


# ---------------------------------------------------------------------------
# A browser that speaks just enough CDP, and remembers everything it was asked
# ---------------------------------------------------------------------------
def fake_jpeg(state: int) -> bytes:
    return b"\xff\xd8FAKE%08d\xff\xd9" % state


class FakeBrowser:
    def __init__(self):
        self.methods = []                 # (method, params, sessionId)
        self.targets = {"T1": 0, "T2": 50000}   # target -> the "page state" its frames show
        self.closed = set()
        self.painting = True
        self.animate = True
        self.rate = 100.0
        self.max_inflight = 3
        self.max_inflight_seen = 0
        self.sent = 0
        self.cast_no = 0
        self.sessions = {}
        self.n = 0
        self.loop = None
        self.port = 0
        self.ready = threading.Event()
        self.th = threading.Thread(target=lambda: asyncio.run(self._main()), daemon=True)

    def start(self):
        self.th.start()
        self.ready.wait(10)
        return self

    def url(self):
        return "ws://127.0.0.1:%d/devtools/browser/fake" % self.port

    def alive(self):
        return {t: {"url": "http://fake/%s" % t, "title": "Fake %s" % t}
                for t in self.targets if t not in self.closed}

    def count(self, method, **match):
        return sum(1 for m, p, s in self.methods if m == method
                   and all(p.get(k) == v for k, v in match.items()))

    async def _main(self):
        self.loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        async with websockets.serve(self._handler, "127.0.0.1", 0, max_size=None,
                                    compression=None) as srv:
            self.port = srv.sockets[0].getsockname()[1]
            self.ready.set()
            await self._stop.wait()

    async def _handler(self, ws):
        try:
            async for raw in ws:
                m = json.loads(raw)
                method, prm, sid = m.get("method"), m.get("params") or {}, m.get("sessionId") or ""
                self.methods.append((method, prm, sid))
                reply = {"id": m["id"], "result": {}}
                if sid:
                    reply["sessionId"] = sid
                after = None
                if method == "Target.attachToTarget":
                    t = prm.get("targetId")
                    if t not in self.targets or t in self.closed:
                        reply = {"id": m["id"], "error": {"code": -32602, "message": "No target with given id found"}}
                    else:
                        self.n += 1
                        s = "S%d" % self.n
                        self.sessions[s] = {"sid": s, "target": t, "ws": ws, "cast": None}
                        reply["result"] = {"sessionId": s}
                elif method == "Page.startScreencast" and sid in self.sessions:
                    sess = self.sessions[sid]
                    if sess["cast"]:
                        sess["cast"]["task"].cancel()
                    self.cast_no += 1
                    cast = {"no": self.cast_no, "inflight": 0, "params": prm}
                    sess["cast"] = cast
                    after = (sess, cast)
                elif method == "Page.stopScreencast" and sid in self.sessions:
                    sess = self.sessions[sid]
                    if sess["cast"]:
                        sess["cast"]["task"].cancel()
                        sess["cast"] = None
                elif method == "Page.screencastFrameAck" and sid in self.sessions:
                    cast = self.sessions[sid]["cast"]
                    if cast and prm.get("sessionId") == cast["no"]:
                        cast["inflight"] = max(0, cast["inflight"] - 1)
                elif method == "Target.detachFromTarget":
                    sess = self.sessions.pop(prm.get("sessionId"), None)
                    if sess and sess["cast"]:
                        sess["cast"]["task"].cancel()
                await ws.send(json.dumps(reply))
                if after:
                    sess, cast = after
                    # Chrome answers a start with one picture of the page as it is, painting or not.
                    await self._emit(sess, cast)
                    cast["task"] = asyncio.create_task(self._produce(sess, cast))
        except Exception:
            pass

    async def _emit(self, sess, cast):
        if cast["inflight"] >= self.max_inflight:
            return
        cast["inflight"] += 1
        self.max_inflight_seen = max(self.max_inflight_seen, cast["inflight"])
        self.sent += 1
        data = base64.b64encode(fake_jpeg(self.targets[sess["target"]])).decode()
        await sess["ws"].send(json.dumps({"method": "Page.screencastFrame",
                                          "params": {"data": data, "sessionId": cast["no"],
                                                     "metadata": {"deviceWidth": 1280, "deviceHeight": 720}},
                                          "sessionId": sess["sid"]}))

    async def _produce(self, sess, cast):
        try:
            while True:
                await asyncio.sleep(1.0 / self.rate)
                if not self.painting:
                    continue
                if self.animate:
                    self.targets[sess["target"]] += 1
                await self._emit(sess, cast)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    def close_target(self, t):
        async def go():
            self.closed.add(t)
            for s, sess in list(self.sessions.items()):
                if sess["target"] != t:
                    continue
                if sess["cast"]:
                    sess["cast"]["task"].cancel()
                self.sessions.pop(s, None)
                await sess["ws"].send(json.dumps({"method": "Inspector.detached",
                                                  "params": {"reason": "target_closed"}, "sessionId": s}))
                await sess["ws"].send(json.dumps({"method": "Target.detachedFromTarget",
                                                  "params": {"sessionId": s, "targetId": t}}))
        asyncio.run_coroutine_threadsafe(go(), self.loop).result(5)

    def stop(self):
        if self.loop:
            self.loop.call_soon_threadsafe(self._stop.set)


class Pull:
    """One viewer's generator, stepped on its own thread the way Starlette's threadpool steps it."""

    def __init__(self, v):
        self.gen = ls.frames(v)
        self.parts = []
        self.ticks = 0
        self.stop = False
        self.done = False
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    def _run(self):
        try:
            for chunk in self.gen:
                if chunk:
                    self.parts.append((time.perf_counter(), chunk))
                else:
                    self.ticks += 1
                if self.stop:
                    break
        finally:
            self.done = True

    def close(self):
        self.stop = True
        self.th.join(3)
        try:
            self.gen.close()
        except Exception:
            pass

    def jpegs(self):
        return parse_parts(b"".join(c for _, c in self.parts))


def parse_parts(blob: bytes) -> list:
    """MJPEG back into JPEGs, split on the boundary the way Chrome splits it (parts carry no length:
    the next part's headers are written before its JPEG exists). An open last part is left out."""
    out = []
    i = blob.find(b"--frame\r\n")
    if i < 0:
        return out
    i += len(b"--frame\r\n")
    while True:
        k = blob.find(b"\r\n\r\n", i)
        if k < 0:
            return out
        nxt = blob.find(b"\r\n--frame\r\n", k + 4)
        if nxt < 0:
            return out
        out.append(blob[k + 4:nxt])
        i = nxt + len(b"\r\n--frame\r\n")


def browser_shown(blob: bytes) -> list:
    """What Chrome can SHOW. It hands a part's pixels to the <img> only once the NEXT part's header
    block is parsed (ImageResource::OnePartInMultipartReceived) — not at the boundary. This rule is
    why two framings showed every frame one frame late; each one is pinned below."""
    out = []
    i = blob.find(b"--frame")
    if i < 0:
        return out
    k = blob.find(b"\r\n\r\n", i)
    if k < 0:
        return out
    start = k + 4
    while True:
        nxt = blob.find(b"--frame", start)
        if nxt < 0:
            return out
        body = blob[start:nxt]
        hk = blob.find(b"\r\n\r\n", nxt)
        if hk < 0:
            return out                     # the next headers have not arrived: nothing shown yet
        out.append(body[:-2] if body.endswith(b"\r\n") else body)
        start = hk + 4


# ---------------------------------------------------------------------------
# Wiring: the fake browser in, the real browser and the user's settings out
# ---------------------------------------------------------------------------
fake = FakeBrowser().start()
calls = {"ensure_browser": 0, "entry": 0}
_saved = {"browser": ls._browser, "alive": ls._alive_targets, "enabled": live.enabled,
          "pref": live._engine_pref, "ensure": review._ensure_browser, "entry": live._entry,
          "consts": {k: getattr(ls, k) for k in ("_LINGER_S", "_IDLE_S", "_KEEPALIVE_S", "_TICK_S",
                                                  "_SETTLE_S", "_VIEWER_STALE_S", "_MAX_VIEWERS",
                                                  "_CONTROL_S", "_REPLACE_GRACE_S")}}


def _no_browser_launch():
    calls["ensure_browser"] += 1
    raise AssertionError("the stream must never launch a browser")


def _counting_entry(*a, **k):
    calls["entry"] += 1
    return _saved["entry"](*a, **k)


ls._browser = lambda: (fake.url(), 1)
ls._alive_targets = lambda port: fake.alive()
live.enabled = lambda: True
live._engine_pref = lambda key, default=None: default
review._ensure_browser = _no_browser_launch
live._entry = _counting_entry
ls._LINGER_S, ls._KEEPALIVE_S, ls._TICK_S, ls._SETTLE_S = 0.6, 0.5, 0.1, 0.2
ls._VIEWER_STALE_S, ls._CONTROL_S = 30.0, 0.05

tmp = Path(tempfile.mkdtemp(prefix="live-stream-test-"))
GAME = tmp / "game"
SUB = GAME / "sub-game"
OTHER = tmp / "other"
for d in (GAME, SUB, OTHER):
    d.mkdir(parents=True, exist_ok=True)
added_keys = []


def put_tab(folder, target, seen=None, opened=None):
    k = live.key_for(str(folder))
    with live._lock:
        live._tabs[k] = {"project": str(folder), "target": target, "url": "http://fake/%s" % target,
                         "device": "desktop", "width": 0, "height": 0,
                         "opened": time.time() if opened is None else opened,
                         "seen": time.time() if seen is None else seen, "how": "", "engine": "three"}
    added_keys.append(k)
    return k


def drop_tabs():
    with live._lock:
        for k in added_keys:
            live._tabs.pop(k, None)
    added_keys.clear()


def reset_casts():
    ls.shutdown(timeout=3.0)
    with ls._reg:
        ls._casts.clear()
        ls._last_stop.clear()


try:
    # ------------------------------------------------------------------ pure pieces
    print("MJPEG framing")
    j = b"\xff\xd8abc\xff\xd9"
    p = ls.part(j)
    check("a part is its JPEG, then the boundary AND the next part's headers, in one write",
          p == j + b"\r\n--frame\r\nContent-Type: image/jpeg\r\n\r\n", p)
    check("the stream opens with the first boundary and the first part's headers",
          ls.OPENING == b"--frame\r\nContent-Type: image/jpeg\r\n\r\n", ls.OPENING)
    back = parse_parts(ls.OPENING + ls.part(j) + ls.part(fake_jpeg(2)) + ls.part(fake_jpeg(3)))
    check("three parts read back by splitting on the boundary", back == [j, fake_jpeg(2), fake_jpeg(3)], back)
    check("a browser shows a frame the moment it is sent",
          browser_shown(ls.OPENING + ls.part(j)) == [j], browser_shown(ls.OPENING + ls.part(j)))
    usual = b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 7\r\n\r\n" + j + b"\r\n"
    check("...which the usual framing (boundary first) could not: nothing shown until the next frame",
          browser_shown(usual) == [], browser_shown(usual))
    closed = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + j + b"\r\n--frame\r\n"
    check("...nor closing each part with the boundary alone",
          browser_shown(closed) == [], browser_shown(closed))
    check("the media type names the boundary the parts use",
          ls.MEDIA_TYPE == "multipart/x-mixed-replace; boundary=frame" and b"\r\n--frame\r\n" in p)
    check("the response is never cached", "no-store" in ls.HEADERS.get("Cache-Control", ""))

    try:
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (200, 80, 40)).save(buf, "JPEG", quality=70)
        check("jpeg_size reads the frame header of a real JPEG", ls.jpeg_size(buf.getvalue()) == (64, 48),
              ls.jpeg_size(buf.getvalue()))
    except ImportError:
        check("jpeg_size (PIL missing, skipped)", True)
    check("jpeg_size says None for bytes that are not a JPEG", ls.jpeg_size(b"hello world") is None)

    print("Parameters: the query, else the setting, always in range")
    check("nothing asked -> the defaults 8 fps, quality 70, 1280 px", ls.params() == {"fps": 8, "quality": 70, "max_w": 1280},
          ls.params())
    check("too low is raised to the floor", ls.params(-5, 1, 10) == {"fps": 8, "quality": 30, "max_w": 320}
          or ls.params(1, 1, 10) == {"fps": 1, "quality": 30, "max_w": 320}, (ls.params(-5, 1, 10), ls.params(1, 1, 10)))
    check("fps 1 is allowed, quality 1 becomes 30, 10 px becomes 320",
          ls.params(1, 1, 10) == {"fps": 1, "quality": 30, "max_w": 320}, ls.params(1, 1, 10))
    check("too high is cut to the ceiling", ls.params(99, 100, 9999) == {"fps": 30, "quality": 95, "max_w": 1920},
          ls.params(99, 100, 9999))
    check("junk means 'the setting'", ls.params("abc", None, "") == {"fps": 8, "quality": 70, "max_w": 1280})
    live._engine_pref = lambda key, default=None: {"live_watch_fps": 12, "live_watch_quality": 500,
                                                   "live_watch_width": 960}.get(key, default)
    check("Settings -> Studio engine gives the defaults, clamped the same way",
          ls.params() == {"fps": 12, "quality": 95, "max_w": 960}, ls.params())
    check("a query still wins over the setting", ls.params(4)["fps"] == 4)
    live._engine_pref = lambda key, default=None: default

    print("Finding the tab without touching it")
    k_game = put_tab(GAME, "T1", seen=time.time() - 100)
    seen_before = live._tabs[k_game]["seen"]
    key, e, how = ls._peek(str(GAME))
    check("the exact folder finds its tab", key == k_game and how == "exact" and e["target"] == "T1", (key, how))
    check("peeking does not stamp the tab's 'seen' clock", live._tabs[k_game]["seen"] == seen_before)
    drop_tabs()
    k_sub = put_tab(SUB, "T1")
    key, e, how = ls._peek(str(GAME))
    check("a tab opened on a game's own sub-folder is found from the workspace", key == k_sub and how == "inside",
          (key, how))
    drop_tabs()
    k_game = put_tab(GAME, "T1")
    key, e, how = ls._peek(str(SUB))
    check("a tab opened on the workspace is found from a folder inside it", key == k_game and how == "contains",
          (key, how))
    key, e, how = ls._peek(str(OTHER))
    check("an unrelated folder finds nothing", key == "" and e is None)
    check("an empty project finds nothing (it would resolve to the backend's own folder)",
          ls._peek("") == ("", None, "") and ls._peek("   ") == ("", None, ""))
    check("a drive root is not 'the workspace' of every game on the drive",
          ls._peek(Path(str(GAME)).anchor)[1] is None, ls._peek(Path(str(GAME)).anchor)[0])
    home_holds_it = live.key_for(str(GAME)).startswith(live.key_for(str(Path.home())) + os.sep)
    check("nor is the home folder" + ("" if home_holds_it else " (skipped: the temp folder is not under home)"),
          (not home_holds_it) or ls._peek(str(Path.home()))[1] is None)
    drop_tabs()
    # A record with no tab is what live._entry leaves behind after ANY call on the path, a refused
    # one too. Taken as the answer, it hid the agent's live tab on the sub-folder.
    put_tab(GAME, "")
    k_sub = put_tab(SUB, "T1")
    key, e, how = ls._peek(str(GAME))
    check("an exact record with no tab does not hide a live tab on the sub-folder", key == k_sub and how == "inside",
          (key, how))
    drop_tabs()
    k_game = put_tab(GAME, "")
    key, e, how = ls._peek(str(GAME))
    check("...and with nothing else, the empty exact record is still the answer (the status reads its stop)",
          key == k_game and how == "exact" and e["target"] == "", (key, how))
    drop_tabs()
    SUB2 = GAME / "sub-two"
    SUB2.mkdir(parents=True, exist_ok=True)
    now = time.time()
    k_a = put_tab(SUB, "T1", seen=now, opened=now - 60)
    k_b = put_tab(SUB2, "T2", seen=now - 30, opened=now - 5)
    first = ls._peek(str(GAME))[0]
    with live._lock:                             # the other agent is the one seen last now
        live._tabs[k_a]["seen"], live._tabs[k_b]["seen"] = now - 30, now
    second = ls._peek(str(GAME))[0]
    check("two sub-games: the same answer however the agents take turns (the one opened last)",
          first == second == k_b, (first, second, k_b))
    drop_tabs()
    deep = SUB / "deeper"
    deep.mkdir(parents=True, exist_ok=True)
    k_near = put_tab(SUB, "T1", opened=now - 60)
    put_tab(deep, "T2", opened=now)
    check("...and the closest folder wins over the newest", ls._peek(str(GAME))[0] == k_near, ls._peek(str(GAME)))
    drop_tabs()

    # ------------------------------------------------------------------ no tab
    print("No tab: 404, and nothing opened")
    st = ls.status(str(OTHER))
    shape = {"live", "url", "streaming", "viewers", "fps_measured", "last_frame_ms_ago", "agent_active"}
    check("the status carries every field the Game tab reads", shape <= set(st), sorted(st))
    check("...and says nobody has it open", st["live"] is False and st["streaming"] is False and st["viewers"] == 0
          and st["agent_active"] is False and st["last_frame_ms_ago"] is None and st["fps_measured"] == 0.0, st)
    v, err, code = ls.open_viewer(str(OTHER))
    check("no tab -> 404 with the sentence the user sees", v is None and code == 404 and err.get("error") == ls.NO_TAB,
          (code, err))
    put_tab(GAME, "T-dead")
    v, err, code = ls.open_viewer(str(GAME))
    check("a tab record whose tab is gone from the browser -> 404 too", v is None and code == 404, (code, err))
    drop_tabs()
    live.enabled = lambda: False
    put_tab(GAME, "T1")
    v, err, code = ls.open_viewer(str(GAME))
    check("the live link switched off -> 404 that says so", v is None and code == 404 and "off" in err.get("error", ""),
          err)
    st = ls.status(str(GAME))
    check("...and the status says it is off", st.get("enabled") is False and st["live"] is False, st)
    live.enabled = lambda: True
    drop_tabs()

    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from asset_studio.routers import live_stream as rls
        app = FastAPI()
        app.include_router(rls.router)
        cli = TestClient(app)
        r = cli.get("/api/live/stream", params={"project": str(OTHER)})
        check("GET /api/live/stream with no tab -> HTTP 404 JSON",
              r.status_code == 404 and r.json().get("error") == ls.NO_TAB, (r.status_code, r.text[:200]))
        r = cli.get("/api/live/stream/status", params={"project": str(OTHER)})
        check("GET /api/live/stream/status -> 200 with live false", r.status_code == 200 and r.json()["live"] is False,
              r.text[:200])
        check("the routes: MJPEG, the Game tab's WebSocket, and the status",
              sorted(x.path for x in rls.router.routes) == ["/api/live/stream", "/api/live/stream/status",
                                                            "/api/live/stream/ws"],
              sorted(x.path for x in rls.router.routes))
        from starlette.websockets import WebSocketDisconnect as _WSD
        with cli.websocket_connect("/api/live/stream/ws?project=" + str(OTHER)) as wsx:
            msg = wsx.receive_json()
            try:
                wsx.receive_bytes()
                code = None
            except _WSD as dx:
                code = dx.code
        check("the WebSocket with no tab: the same JSON, then a close with 4404",
              msg.get("error") == ls.NO_TAB and code == 4404, (msg, code))
    except Exception as ex:                                        # noqa: BLE001
        check("the router mounts and answers (TestClient)", False, ex)

    # ------------------------------------------------------------------ streaming
    print("A viewer, against the fake browser")
    reset_casts()
    k_game = put_tab(GAME, "T1")
    seen0 = live._tabs[k_game]["seen"]
    fake.painting, fake.animate, fake.rate = True, True, 100.0
    t0 = time.perf_counter()
    v1, err, code = ls.open_viewer(str(GAME))
    check("a live tab -> a viewer", v1 is not None and code == 200, (code, err))
    a = Pull(v1)
    check("the first frame arrives within a second", wait_for(lambda: len(a.parts) >= 1, 1.5),
          len(a.parts))
    first_ms = (a.parts[0][0] - t0) * 1000 if a.parts else -1
    check("...and it is one well-formed MJPEG part", a.parts and parse_parts(a.parts[0][1])
          and parse_parts(a.parts[0][1])[0].startswith(b"\xff\xd8FAKE"), a.parts[:1])
    check("...that a browser shows on its own, before any second frame",
          a.parts and len(browser_shown(a.parts[0][1])) == 1, a.parts[:1])
    time.sleep(0.3)
    c = ls._casts.get(k_game)
    r0, p0, s0 = c.received, c.published, fake.sent
    time.sleep(1.5)
    r1, p1, s1 = c.received, c.published, fake.sent
    pub_rate = (p1 - p0) / 1.5
    sent_rate = (s1 - s0) / 1.5
    check("a page painting 100 frames a second reaches the viewer at about 8 (the setting)",
          4.0 <= pub_rate <= 12.0, round(pub_rate, 1))
    check("and Chrome is only asked to encode about that many (acks are paced), not 100",
          sent_rate <= 16.0, round(sent_rate, 1))
    check("never more than three frames in flight", fake.max_inflight_seen <= 3, fake.max_inflight_seen)
    check("every frame is acked (all but the few held in flight)", c.acked >= c.received - 3, (c.acked, c.received))
    st = ls.status(str(GAME))
    check("status while streaming: live, streaming, one viewer, a measured rate, a recent frame",
          st["live"] and st["streaming"] and st["viewers"] == 1 and st["fps_measured"] > 0
          and st["last_frame_ms_ago"] is not None and st["last_frame_ms_ago"] < 1000, st)
    check("status names the engine and how the tab matched", st.get("engine") == "three" and st.get("match") == "exact", st)
    check("an agent call 0 s ago reads as an agent working", st["agent_active"] is True)
    check("watching never stamped the tab's 'seen' clock", live._tabs[k_game]["seen"] == seen0)

    print("Refcount: one screencast, however many watch")
    v2, _, _ = ls.open_viewer(str(GAME))
    b = Pull(v2)
    check("the second viewer gets frames", wait_for(lambda: len(b.parts) >= 2, 2.0), len(b.parts))
    check("...from the SAME screencast: one attach, one start", fake.count("Target.attachToTarget") == 1
          and fake.count("Page.startScreencast") == 1, (fake.count("Target.attachToTarget"), fake.count("Page.startScreencast")))
    check("two viewers counted", ls.status(str(GAME))["viewers"] == 2)
    a.close()
    check("one leaves -> one viewer, still streaming", wait_for(lambda: ls.status(str(GAME))["viewers"] == 1, 1.0)
          and ls.status(str(GAME))["streaming"])
    v3, _, _ = ls.open_viewer(str(GAME))
    check("somebody joins again -> the same cast, no new attach", ls._casts.get(k_game) is c
          and fake.count("Target.attachToTarget") == 1)
    ls.leave(v3)
    b.close()
    check("the last one leaves -> the screencast lingers a moment",
          ls.status(str(GAME))["streaming"] is True)
    check("...then stops (linger)", wait_for(lambda: not ls.status(str(GAME))["streaming"], 3.0))
    st = ls.status(str(GAME))
    check("the status says why", (st.get("stopped") or {}).get("why") == "nobody is watching", st.get("stopped"))
    check("it stopped the screencast and detached", fake.count("Page.stopScreencast") >= 1
          and fake.count("Target.detachFromTarget") >= 1)
    check("nothing is left in the registry", k_game not in ls._casts)
    check("the thread is gone", wait_for(lambda: not c.thread.is_alive(), 3.0))

    print("A page that stops painting: one refresh, the LAST picture, then keepalives")
    reset_casts()
    fake.painting, fake.animate = True, True
    v1, _, _ = ls.open_viewer(str(GAME))
    a = Pull(v1)
    wait_for(lambda: len(a.parts) >= 3, 2.0)
    c = ls._casts.get(k_game)
    starts0 = fake.count("Page.startScreencast")
    fake.painting = False
    final_state = fake.targets["T1"]
    check("the stream settles with one refresh", wait_for(lambda: c.refreshes >= 1, 2.0), c.refreshes)
    time.sleep(0.4)
    check("the refresh is a restart of the screencast (a start is how Chrome gives a fresh picture)",
          fake.count("Page.startScreencast") == starts0 + 1, fake.count("Page.startScreencast") - starts0)
    check("the newest frame is the page as it is now, not one from the burst",
          c.frame == fake_jpeg(final_state), (c.frame, fake_jpeg(final_state)))
    sent_idle0 = fake.sent
    n_parts0 = len(a.parts)
    time.sleep(1.3)
    check("a still page costs Chrome nothing (no frames sent)", fake.sent - sent_idle0 <= 1, fake.sent - sent_idle0)
    later = a.jpegs()[n_parts0:]
    check("the viewer still gets the picture again every half second (keepalive)",
          len(later) >= 2 and all(x == fake_jpeg(final_state) for x in later), len(later))
    check("no second refresh while it stays still", c.refreshes == 1, c.refreshes)

    print("A scene that paints the same picture over and over")
    fake.animate = False
    fake.painting = True
    time.sleep(0.4)
    p_same, r_same = c.published, c.received
    time.sleep(1.0)
    check("Chrome keeps sending frames", c.received > r_same, c.received - r_same)
    check("identical frames are acked but not sent to viewers again", c.published == p_same, c.published - p_same)
    fake.animate = True
    check("the moment it changes, frames flow again", wait_for(lambda: c.published > p_same + 2, 2.0))
    a.close()
    reset_casts()

    print("The agent's tab closes under the stream")
    fake.painting, fake.animate = True, True
    v1, _, _ = ls.open_viewer(str(GAME))
    a = Pull(v1)
    wait_for(lambda: len(a.parts) >= 2, 2.0)
    attaches = fake.count("Target.attachToTarget")
    fake.close_target("T1")
    check("the viewer's stream ends by itself", wait_for(lambda: a.done, 2.5))
    st = ls.status(str(GAME))
    check("the status says it stopped because the tab closed",
          not st["streaming"] and "closed" in (st.get("stopped") or {}).get("why", ""), st.get("stopped"))
    check("it did not try to re-open the dead tab", fake.count("Target.attachToTarget") == attaches)
    check("...and the tab is no longer live", st["live"] is False)
    a.close()
    fake.closed.discard("T1")
    reset_casts()

    print("The agent replaces its tab (open with reload): the stream follows")
    v1, _, _ = ls.open_viewer(str(GAME))
    a = Pull(v1)
    wait_for(lambda: len(a.parts) >= 2, 2.0)
    c = ls._casts.get(k_game)
    with live._lock:
        live._tabs[k_game]["target"] = "T2"
    check("it attaches to the new tab", wait_for(lambda: fake.count("Target.attachToTarget", targetId="T2") == 1, 2.0))
    check("frames now come from the new tab", wait_for(
        lambda: a.jpegs() and int(a.jpegs()[-1][6:14]) >= 50000, 2.0), a.jpegs()[-1:] if a.parts else None)
    check("one switch counted, same viewer, still streaming", c.switches == 1 and ls.status(str(GAME))["viewers"] == 1)
    a.close()
    reset_casts()
    with live._lock:
        live._tabs[k_game]["target"] = "T1"

    # THE REAL SEQUENCE of a replacement: the record has NO tab for a while (an open in progress
    # measured 1.6 s), then the new one. Stopping on the empty record ended the person's stream.
    print("The record is empty for a while, then names the new tab: the stream waits, then follows")
    v1, _, _ = ls.open_viewer(str(GAME))
    a = Pull(v1)
    wait_for(lambda: len(a.parts) >= 2, 2.0)
    c = ls._casts.get(k_game)
    with live._lock:
        live._tabs[k_game]["target"] = ""
    time.sleep(1.7)
    check("an empty record for 1.7 s does not stop the stream", not a.done and not c.stopping, c.why)
    with live._lock:
        live._tabs[k_game]["target"] = "T2"
    check("...and when the new tab is named, it attaches to it",
          wait_for(lambda: fake.count("Target.attachToTarget", targetId="T2") >= 1, 2.0))
    check("...same viewer, one switch", wait_for(lambda: c.switches == 1, 1.0) and not a.done, (c.switches, a.done))
    ls._REPLACE_GRACE_S = 0.5
    with live._lock:
        live._tabs[k_game]["target"] = ""
    check("a record that STAYS empty past the grace ends the stream", wait_for(lambda: a.done, 3.0))
    check("...and says the tab was closed", "closed" in (ls._last_stop.get(k_game) or {}).get("why", ""),
          ls._last_stop.get(k_game))
    ls._REPLACE_GRACE_S = _saved["consts"]["_REPLACE_GRACE_S"]
    a.close()
    reset_casts()
    with live._lock:
        live._tabs[k_game]["target"] = "T1"

    print("Ten minutes with no agent: the stream stops even with a viewer")
    ls._IDLE_S = 0.5
    with live._lock:
        live._tabs[k_game]["seen"] = time.time() - 3600
    v1, _, _ = ls.open_viewer(str(GAME))
    a = Pull(v1)
    check("it stops for idle", wait_for(lambda: a.done, 3.0))
    check("the status says idle", (ls.status(str(GAME)).get("stopped") or {}).get("why") == "idle",
          ls.status(str(GAME)).get("stopped"))
    check("an agent quiet for an hour is not 'working'", ls.status(str(GAME))["agent_active"] is False)
    a.close()
    ls._IDLE_S = _saved["consts"]["_IDLE_S"]
    with live._lock:
        live._tabs[k_game]["seen"] = time.time()
    reset_casts()

    print("Off means off, for a stream already running")
    v1, _, _ = ls.open_viewer(str(GAME))
    a = Pull(v1)
    wait_for(lambda: len(a.parts) >= 1, 2.0)
    live.enabled = lambda: False
    check("switching the live link off ends the stream", wait_for(lambda: a.done, 2.5))
    check("...and says so", (ls.status(str(GAME)).get("stopped") or {}).get("why") == "the live link was switched off"
          or ls._last_stop.get(k_game, {}).get("why") == "the live link was switched off",
          ls._last_stop.get(k_game))
    live.enabled = lambda: True
    a.close()
    reset_casts()

    print("Limits and leftovers")
    ls._MAX_VIEWERS = 2
    va, _, _ = ls.open_viewer(str(GAME))
    vb, _, _ = ls.open_viewer(str(GAME))
    vc, err, code = ls.open_viewer(str(GAME))
    check("more viewers than the cap -> 429, not a starved threadpool", vc is None and code == 429, (code, err))
    ls.leave(va)
    ls.leave(vb)
    ls._MAX_VIEWERS = _saved["consts"]["_MAX_VIEWERS"]
    reset_casts()
    ls._VIEWER_STALE_S = 0.4
    vs, _, _ = ls.open_viewer(str(GAME))          # opened, never pulled: Starlette dropped it
    c = ls._casts.get(k_game)
    check("a viewer nobody pulls is dropped", wait_for(lambda: ls.status(str(GAME))["viewers"] == 0, 2.0))
    check("...and the screencast then stops after its linger", wait_for(lambda: c.stopping, 2.5))
    ls._VIEWER_STALE_S = 30.0
    reset_casts()

    print("The most demanding viewer sets the picture")
    va, _, _ = ls.open_viewer(str(GAME), 8, 50, 640)
    pa = Pull(va)
    wait_for(lambda: len(pa.parts) >= 1, 1.5)
    vb, _, _ = ls.open_viewer(str(GAME), 8, 80, 1280)
    check("the screencast restarts at the higher quality and width",
          wait_for(lambda: fake.count("Page.startScreencast", quality=80, maxWidth=1280) >= 1, 2.0),
          [p for m, p, s in fake.methods if m == "Page.startScreencast"][-2:])
    check("maxHeight bounds the long edge with the same number",
          fake.count("Page.startScreencast", maxHeight=1280) >= 1)
    ls.leave(vb)
    pa.close()
    reset_casts()

    # THE GAME TAB'S TRANSPORT. The MJPEG response never ends and held one of the browser's six
    # connections to the Studio for as long as the view was open; the same frames over a WebSocket.
    print("The Game tab's WebSocket: the same frames, and the viewer let go on close")
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from asset_studio.routers import live_stream as rls
        wapp = FastAPI()
        wapp.include_router(rls.router)
        wcli = TestClient(wapp)
        fake.painting, fake.animate = True, True
        with wcli.websocket_connect("/api/live/stream/ws?project=" + str(GAME)) as wsx:
            got = [wsx.receive_bytes() for _ in range(3)]
            check("three binary messages, each one whole JPEG", all(g.startswith(b"\xff\xd8FAKE") for g in got),
                  [g[:12] for g in got])
            check("...one viewer, counted like any other", ls.status(str(GAME))["viewers"] == 1,
                  ls.status(str(GAME))["viewers"])
        check("closing the socket lets the viewer go", wait_for(lambda: ls.status(str(GAME))["viewers"] == 0, 3.0),
              ls.status(str(GAME))["viewers"])
    except Exception as ex:                                        # noqa: BLE001
        check("the WebSocket streams (TestClient)", False, repr(ex))
    reset_casts()

    print("Paced acks, unit by unit (no browser)")
    loop = asyncio.new_event_loop()

    class _WS:
        async def send(self, m):
            pass

    cu = ls._Cast("unit", "unit", "TU", "ws://unused", {"fps": 10, "quality": 70, "max_w": 1280})
    cu._loop, cu._ws, cu._sid = loop, _WS(), "SU"
    cu._refresh_pending = False
    for i in range(10):
        cu._on_frame({"sessionId": 7, "data": base64.b64encode(fake_jpeg(i)).decode()})
    check("a burst of ten: one frame out at once, one ack", cu.published == 1 and cu.acked == 1, (cu.published, cu.acked))
    check("...the one waiting is the NEWEST, not the second", cu._pending == base64.b64encode(fake_jpeg(9)).decode())
    cu._last_tick -= 1.0
    cu._timer = None
    cu._on_timer()
    check("at the next tick the newest goes out and one more slot is freed",
          cu.frame == fake_jpeg(9) and cu.published == 2 and cu.acked == 2, (cu.frame, cu.published, cu.acked))
    cu._on_frame({"sessionId": 7, "data": base64.b64encode(fake_jpeg(20)).decode()})
    cu._on_frame({"sessionId": 7, "data": base64.b64encode(fake_jpeg(9)).decode()})
    check("a flicker back to what viewers already have cancels the frame in between", cu._pending is None)
    acked = cu.acked
    cu._on_frame({"sessionId": 6, "data": base64.b64encode(fake_jpeg(99)).decode()})
    check("a straggler from a restarted screencast is acked at once and never shown",
          cu.acked == acked + 1 and cu._pending is None and cu.frame == fake_jpeg(9))
    cu._on_frame({"sessionId": 8, "data": base64.b64encode(fake_jpeg(30)).decode()})
    check("a newer screencast number voids the old held acks", list(cu._held) in ([8], []), list(cu._held))
    # The acks became tasks on a loop that never ran: cancel them and let the loop finish them, so
    # nothing is left pending when it closes.
    left = asyncio.all_tasks(loop)
    for t in left:
        t.cancel()
    if left:
        loop.run_until_complete(asyncio.gather(*left, return_exceptions=True))
    loop.close()

    print("What the stream sent to the browser, in every test above")
    allowed = {"Target.attachToTarget", "Target.detachFromTarget", "Page.startScreencast",
               "Page.stopScreencast", "Page.screencastFrameAck"}
    used = sorted({m for m, _, _ in fake.methods})
    check("only attach, start, ack, stop and detach — never navigate, reload, evaluate or create",
          set(used) <= allowed, used)
    check("it never launched a browser", calls["ensure_browser"] == 0, calls["ensure_browser"])
    check("it never called live._entry (which would stamp 'seen')", calls["entry"] == 0, calls["entry"])
    check("first frame latency, measured", first_ms >= 0, round(first_ms))
    print("        first frame after %.0f ms; %d commands to the fake browser" % (first_ms, len(fake.methods)))
finally:
    try:
        reset_casts()
    except Exception:
        pass
    drop_tabs()
    ls._browser, ls._alive_targets = _saved["browser"], _saved["alive"]
    live.enabled, live._engine_pref, live._entry = _saved["enabled"], _saved["pref"], _saved["entry"]
    review._ensure_browser = _saved["ensure"]
    for k, val in _saved["consts"].items():
        setattr(ls, k, val)
    fake.stop()
    shutil.rmtree(tmp, ignore_errors=True)

print("\n  %d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

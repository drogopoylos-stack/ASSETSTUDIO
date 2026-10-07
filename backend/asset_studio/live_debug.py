# -*- coding: utf-8 -*-
"""A debugger for asset code: stop where it went wrong and read the variables.

Godot's MCP has breakpoints, a stack and locals, and it is the single best thing in any of the
three editor integrations. Blender's cannot have one — its whole surface is `execute_blender_code`
and a screenshot. Unity's cannot either. Ours can, because the code we are debugging runs in a
Chrome we already drive, and CDP has had a Debugger domain for a decade.

WHY THIS IS ONE REQUEST AND NOT A SESSION.

The live module is deliberately stateless: `asyncio.run` per HTTP request, the socket closed in a
`finally`, and only the TAB persisting between calls. That is a good design — it is why a backend
restart cannot strand a connection — and a debugger that held a socket across requests would have
to break it.

It does not need to. "Why is this vertex NaN" is answered inside a single run: enable the domain,
arm the traps, evaluate, and when the page stops, read the frames and the variables before letting
it go. The whole conversation is one round trip and the page is never left paused.

THE CLIENT HERE IS NOT THE ONE IN review.py. That one loops on `recv()` until it sees its own id
and drops everything else — which is correct for request-response and fatal here, because
`Debugger.paused` IS an event. So this module has its own small client with a receive pump, a map
of pending replies, and an event queue. It is used only for the length of one debug run.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from . import live as L
from . import review
from .config import settings

# A paused page holds the whole tab. Every wait here is bounded so a runaway script cannot leave
# the browser stopped with nobody coming back for it.
_PAUSE_WAIT = 20.0
_CALL_WAIT = 20.0
_FINISH_WAIT = 30.0
_MAX_STEPS = 40
_MAX_FRAMES = 12
_MAX_LOCALS = 60
_VAL = 200               # how much of one value's description to keep


class _Pump:
    """A CDP client that can hear the browser speak first.

    `id` replies go to whoever asked; everything without an id is an event and goes on the queue.
    """

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.n = 0
        self.pending: dict = {}
        self.events: asyncio.Queue = asyncio.Queue()
        self.task: Optional[asyncio.Task] = None
        self.dead: Optional[str] = None

    def start(self) -> None:
        self.task = asyncio.ensure_future(self._pump())

    async def _pump(self) -> None:
        try:
            while True:
                msg = json.loads(await self.ws.recv())
                mid = msg.get("id")
                if mid is None:
                    await self.events.put(msg)
                    continue
                fut = self.pending.pop(mid, None)
                if fut is None or fut.done():
                    continue
                if "error" in msg:
                    fut.set_exception(RuntimeError(str(msg["error"].get("message") or msg["error"])))
                else:
                    fut.set_result(msg.get("result") or {})
        except asyncio.CancelledError:                       # normal teardown
            raise
        except Exception as ex:                              # noqa: BLE001
            self.dead = str(ex)
            for fut in list(self.pending.values()):
                if not fut.done():
                    fut.set_exception(RuntimeError("the connection closed: %s" % ex))
            self.pending.clear()

    async def send(self, method: str, params: Optional[dict] = None, session: str = ""):
        """Fire a command and hand back the future, WITHOUT waiting.

        The reason this is separate from `call`: a `Runtime.evaluate` that hits a breakpoint does
        not reply until the page is resumed, so its reply has to be raced against the pause event
        rather than awaited.
        """
        self.n += 1
        msg: dict = {"id": self.n, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self.pending[self.n] = fut
        await self.ws.send(json.dumps(msg))
        return fut

    async def call(self, method: str, params: Optional[dict] = None, session: str = "",
                   timeout: float = _CALL_WAIT) -> dict:
        fut = await self.send(method, params, session)
        return await asyncio.wait_for(fut, timeout)

    async def wait_for(self, name: str, timeout: float) -> Optional[dict]:
        """The next event with this method name, or None if it does not come in time."""
        loop = asyncio.get_event_loop()
        end = loop.time() + timeout
        while True:
            left = end - loop.time()
            if left <= 0:
                return None
            try:
                msg = await asyncio.wait_for(self.events.get(), left)
            except asyncio.TimeoutError:
                return None
            if msg.get("method") == name:
                return msg.get("params") or {}

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):      # noqa: BLE001
                pass


def _short(v: dict) -> Any:
    """One CDP RemoteObject as something readable, without fetching its whole tree."""
    if not isinstance(v, dict):
        return v
    if "value" in v and v.get("type") in ("string", "number", "boolean"):
        s = v["value"]
        return s[:_VAL] if isinstance(s, str) else s
    if v.get("type") == "undefined":
        return "undefined"
    if v.get("subtype") == "null":
        return None
    d = v.get("description") or v.get("className") or v.get("type") or "?"
    return str(d)[:_VAL]


async def _locals_of(p: _Pump, sid: str, frame: dict) -> dict:
    """The variables in scope at one call frame — the whole point of stopping.

    Only the LOCAL scope. The closure and global scopes are usually the entire engine and would
    bury the six numbers actually being looked at.
    """
    for sc in frame.get("scopeChain") or []:
        if sc.get("type") != "local":
            continue
        oid = ((sc.get("object") or {}).get("objectId"))
        if not oid:
            continue
        try:
            got = await p.call("Runtime.getProperties",
                               {"objectId": oid, "ownProperties": True, "generatePreview": False},
                               sid)
        except Exception as ex:                              # noqa: BLE001
            return {"__error": str(ex)[:200]}
        out: dict = {}
        for pr in (got.get("result") or [])[:_MAX_LOCALS]:
            out[pr.get("name", "?")] = _short(pr.get("value") or {})
        return out
    return {}


async def _snapshot(p: _Pump, sid: str, params: dict, depth: int) -> dict:
    """What the page looks like at the moment it stopped."""
    frames = (params.get("callFrames") or [])[:_MAX_FRAMES]
    stack = []
    for f in frames:
        loc = f.get("location") or {}
        stack.append({
            "fn": f.get("functionName") or "(anonymous)",
            # CDP counts from zero; a person counts from one, and the agent wrote the line.
            "line": int(loc.get("lineNumber", 0)) + 1,
            "col": int(loc.get("columnNumber", 0)) + 1,
            "url": (f.get("url") or "").split("/")[-1],
        })
    out: dict = {
        "reason": params.get("reason") or "other",
        "stack": stack,
        "where": stack[0] if stack else {},
    }
    if params.get("reason") == "exception":
        out["exception"] = _short((params.get("data") or {}))
    if frames:
        out["locals"] = await _locals_of(p, sid, frames[0])
        if depth > 1:
            deeper = []
            for f in frames[1:depth]:
                deeper.append({"fn": f.get("functionName") or "(anonymous)",
                               "locals": await _locals_of(p, sid, f)})
            out["callers"] = deeper
    return out


def debug(project: str, js: str, pause_on: str = "uncaught", lines: Optional[list] = None,
          steps: int = 0, depth: int = 1, params: Optional[dict] = None) -> dict:
    """Run asset code with the debugger armed, and report every stop.

    `pause_on`  "uncaught" (the default), "all", or "none" if you only want the breakpoints.
    `lines`     1-based line numbers IN YOUR OWN CODE to break on, before it runs.
    `steps`     after the first stop, step over this many statements, reporting each.
    `depth`     how many frames up the stack to read locals from. 1 is the frame that stopped.

    Returns the stops in order, then whatever the code finally produced. The page is always
    resumed, including when something here fails — a tab left paused is a wedged browser.
    """
    if not settings.get("cc_debugger", False):
        return {"ok": False,
                "error": "The debugger is off. Turn it on in Settings → Studio engine → Debugger."}
    bad = L._guard(project)
    if bad:
        return bad
    if not str(js).strip():
        return {"ok": False, "error": "nothing to run"}

    entry = L._entry(project)
    if not entry or not entry.get("target"):
        return {"ok": False,
                "error": "no page open for this project — POST /api/live/open or run the forge first"}

    want_lines = [int(n) for n in (lines or []) if isinstance(n, (int, float))][:20]
    steps = max(0, min(_MAX_STEPS, int(steps or 0)))
    depth = max(1, min(6, int(depth or 1)))
    state = pause_on if pause_on in ("uncaught", "all", "none") else "uncaught"

    # The same wrapper the forge uses, so a line number here means the same line there — and the
    # same sourceURL, so a breakpoint has something to attach to.
    body = ("(async()=>{const __c=__forge.ctx();"
            "const {pc,THREE,engine,app,device,renderer,scene,camera,root,forge,add,clear,log,"
            "params}=__c;"
            "const __m=__live.mark();"
            "let __v,__e;try{__v=await (async()=>{%s})();}catch(err){__e=String(err&&err.stack||err);}"
            "return JSON.stringify({v:__live.cap(__v,3,4000),e:__e,c:__live.since(__m)});})()"
            "\n//# sourceURL=studio-forge.js" % js)

    async def go() -> dict:
        ws = await L._connect(review._ensure_browser())
        p = _Pump(ws)
        p.start()
        stops: list = []
        resumed = True
        # Declared before the attach, because the teardown below runs even when the attach is what
        # failed — and resuming with an empty session id would silently do nothing.
        sid = ""
        try:
            sid = (await p.call("Target.attachToTarget",
                                {"targetId": entry["target"], "flatten": True}))["sessionId"]
            await p.call("Runtime.enable", {}, sid)
            await p.call("Debugger.enable", {}, sid)
            if state != "none":
                await p.call("Debugger.setPauseOnExceptions", {"state": state}, sid)

            armed = []
            for n in want_lines:
                try:
                    r = await p.call("Debugger.setBreakpointByUrl", {
                        # The agent's line 1 is line 0 of the wrapper, because its code is
                        # interpolated inline rather than on a line of its own.
                        "lineNumber": max(0, n - 1),
                        "urlRegex": "studio-forge\\.js",
                    }, sid)
                    armed.append({"line": n, "id": r.get("breakpointId", ""),
                                  "resolved": len(r.get("locations") or [])})
                except Exception as ex:                      # noqa: BLE001
                    armed.append({"line": n, "error": str(ex)[:160]})

            if params:
                await p.call("Runtime.evaluate",
                             {"expression": "__forge.params = %s" % json.dumps(params),
                              "returnByValue": True}, sid)

            # Fired, NOT awaited: if it hits a trap the reply does not come until we resume.
            done = await p.send("Runtime.evaluate",
                                {"expression": body, "returnByValue": True, "awaitPromise": True},
                                sid)

            left = steps
            while True:
                pause_task = asyncio.ensure_future(p.wait_for("Debugger.paused", _PAUSE_WAIT))
                finished, _ = await asyncio.wait(
                    [pause_task, done], timeout=_FINISH_WAIT + 1,
                    return_when=asyncio.FIRST_COMPLETED)
                if done in finished:
                    pause_task.cancel()
                    break
                got = pause_task.result() if pause_task in finished else None
                if not got:
                    resumed = False
                    break
                resumed = False
                stops.append(await _snapshot(p, sid, got, depth))
                if left > 0:
                    left -= 1
                    await p.call("Debugger.stepOver", {}, sid)
                else:
                    await p.call("Debugger.resume", {}, sid)
                    resumed = True

            out: dict = {"ok": True, "stops": stops, "breakpoints": armed,
                         "paused": len(stops), "pause_on": state}
            try:
                res = await asyncio.wait_for(done, _FINISH_WAIT)
                raw = ((res.get("result") or {}).get("value")) or "{}"
                ran = json.loads(raw) if isinstance(raw, str) else {}
                if ran.get("e"):
                    out["ok"] = False
                    out["error"] = ran["e"][:1200]
                if ran.get("v") is not None:
                    out["value"] = ran["v"]
                if ran.get("c"):
                    rank = {"error": 0, "promise": 0, "resource": 0, "net": 0, "warn": 1}
                    out["console"] = sorted(ran["c"], key=lambda r: rank.get(r.get("kind"), 2))[:30]
            except asyncio.TimeoutError:
                out["ok"] = False
                out["error"] = "the code did not finish within %ds of being resumed" % int(_FINISH_WAIT)
            return out
        finally:
            # ALWAYS let the page go. A tab left paused looks to everything else like a browser
            # that has hung, including to the next agent that tries to render anything.
            if sid and not resumed:
                try:
                    await p.call("Debugger.resume", {}, sid, timeout=4.0)
                except Exception:                            # noqa: BLE001
                    pass
            if sid:
                try:
                    await p.call("Debugger.disable", {}, sid, timeout=4.0)
                except Exception:                            # noqa: BLE001
                    pass
            await p.close()
            try:
                await ws.close()
            except Exception:                                # noqa: BLE001
                pass

    try:
        return L._run(go)
    except Exception as ex:                                  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(ex).__name__, str(ex)[:600])}

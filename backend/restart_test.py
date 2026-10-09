"""Does a restart actually replace the backend, and can the launcher find what to end?

Three things went wrong together and each is covered here.

The desktop launcher spawns `.venv/Scripts/python.exe`, which on Windows is a virtualenv
REDIRECTOR: it starts the real interpreter as a child and stays alive as its parent. The pid the
launcher held was the stub's, so killing it left the real backend running and still holding port
8777 — and /api/health then answered from the survivor, so the launcher concluded the backend was
"already up" and started nothing. A restart reported success and changed nothing.

`os.execv` was the second half. On Windows it is not an image replacement: the C runtime creates
a new process and then exits this one, so both exist at once and the replacement can reach bind()
while the old one still owns the socket. Under the launcher it was worse — the ending process
triggered a respawn, and several backends then raced for the port.

Nothing here binds 8777 or touches a running Studio.
"""
import io
import os
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, extra))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


print("The launcher can find the process that is really serving")
from asset_studio import main as app_main            # noqa: E402

import asyncio                                       # noqa: E402

h = asyncio.run(app_main.health())                   # async since 2026-10-09: see main.health
check("/api/health names a pid", isinstance(h.get("pid"), int) and h["pid"] > 0, h.get("pid"))
check("...and it is this process", h["pid"] == os.getpid(), "%s vs %s" % (h.get("pid"), os.getpid()))
check("the old fields are untouched", h.get("ok") is True and "version" in h, sorted(h))

print("\nA replacement waits for the port instead of dying on it")
port = free_port()
t0 = time.time()
app_main._wait_for_port("127.0.0.1", port, timeout=5.0)
check("a free port returns at once", time.time() - t0 < 0.5, "%.2fs" % (time.time() - t0))

def serving(port: int):
    """A stand-in for the backend being replaced: it listens AND accepts.

    Accepting matters. A listener that never accepts fills its backlog after one connection and
    the OS then refuses the next — which reads as "nothing is listening" and is not how a real
    uvicorn behaves. Modelling the wrong thing here would have tested the check against a
    condition it will never meet.
    """
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(16)
    stop = threading.Event()

    def loop():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                c, _ = srv.accept()
                c.close()
            except OSError:
                continue
        srv.close()

    threading.Thread(target=loop, daemon=True).start()
    return stop


stop = serving(port)
threading.Timer(1.2, stop.set).start()      # hold the port, as a backend being replaced does
t0 = time.time()
app_main._wait_for_port("127.0.0.1", port, timeout=6.0)
held = time.time() - t0
check("a busy port is waited out", held > 0.9, "%.2fs" % held)
check("...and it returns once the port frees, not on the timeout", held < 5.0, "%.2fs" % held)

# CONNECT is the test, not bind: on Windows SO_REUSEADDR lets a second socket bind a port that is
# already listening, so a bind test would have called a busy port free and crashed the successor.
stop2 = serving(port)
try:
    dup = socket.socket()
    dup.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    rebound = True
    try:
        dup.bind(("127.0.0.1", port))
    except OSError:
        rebound = False
    dup.close()
    t0 = time.time()
    app_main._wait_for_port("127.0.0.1", port, timeout=1.5)
    check("a listening port is seen as busy even where rebinding is allowed",
          time.time() - t0 >= 1.4, "rebind_allowed=%s waited=%.2fs" % (rebound, time.time() - t0))
finally:
    stop2.set()
    time.sleep(0.4)

print("\nThe replacement is spawned, not exec'd")
from asset_studio.routers import system as sysmod    # noqa: E402

src = open("asset_studio/routers/system.py", encoding="utf-8").read()
# The word still appears — the comment explains why it was dropped. What must not exist is a CALL.
check("nothing calls os.execv any more", "os.execv(" not in src)

calls, exits = [], []
real_popen, real_exit = subprocess.Popen, os._exit
sysmod.subprocess.Popen = lambda *a, **k: (calls.append((a, k)), object())[1]
sysmod.os._exit = lambda code: exits.append(code)
try:
    sysmod._relaunch()
finally:
    sysmod.subprocess.Popen, sysmod.os._exit = real_popen, real_exit

check("exactly one replacement is started", len(calls) == 1, len(calls))
argv = calls[0][0][0] if calls else []
kw = calls[0][1] if calls else {}
check("it runs the module, not a shell string", argv[1:] == ["-m", "asset_studio.main"], argv)
check("from the folder where that module resolves",
      os.path.basename(str(kw.get("cwd", ""))) == "backend", kw.get("cwd"))
check("detached, so it is not a child that dies with us",
      os.name != "nt" or kw.get("creationflags", 0) != 0, kw.get("creationflags"))
check("and only THEN does this process exit", exits == [0], exits)

print("\nA spawn that fails must not take the backend down with it")
calls.clear()
exits.clear()


def boom(*a, **k):
    calls.append(1)
    raise OSError("no")


sysmod.subprocess.Popen = boom
sysmod.os._exit = lambda code: exits.append(code)
try:
    sysmod._relaunch()
finally:
    sysmod.subprocess.Popen, sysmod.os._exit = real_popen, real_exit

check("both spawn attempts are made", len(calls) == 2, len(calls))
check("nothing exits, so the old process keeps serving", exits == [], exits)

print("\nA duplicate refuses to start, and refuses to tidy up after the real one")
# uvicorn runs the start-up handlers, THEN binds, and on a bind failure runs the SHUTDOWN
# handlers. So a backend that lost the race for the port adopted the live sessions and then, on
# its way out, closed the idle ones. A real conversation ended that way: a message was queued to
# it, the process that owned it was closed by a backend that had never served a single request,
# and no reply ever came. Both halves are covered here.
import http.server                                   # noqa: E402

hp = free_port()


class _Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"ok": true, "version": "0.1.0", "pid": 1}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


httpd = http.server.HTTPServer(("127.0.0.1", hp), _Health)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
try:
    check("a healthy backend on the port is detected", app_main._already_serving("127.0.0.1", hp))
finally:
    httpd.shutdown()
    httpd.server_close()
check("a dead port is not", app_main._already_serving("127.0.0.1", free_port()) is False)

served = app_main._SERVED
try:
    app_main._SERVED = False
    check("a process that never served may not close the sessions",
          app_main._may_close_sessions() is False)
    app_main._SERVED = True
    check("one that did serve still tidies up after itself",
          app_main._may_close_sessions() is True)
finally:
    app_main._SERVED = served

body = open("asset_studio/main.py", encoding="utf-8").read()
check("shutdown_all is behind that guard, not called unconditionally",
      "_may_close_sessions()" in body.split("cc_session.shutdown_all")[0].rsplit("try:", 1)[-1])


# ---------------------------------------------------------------------------
print("\nA RESTART WARNS ABOUT WORK AT RISK, AND ONLY ABOUT WORK AT RISK")
# The warning turns on one fact: is somebody other than this backend holding the session's stdin?
# With a keeper, a restart does not touch the process, so warning about its agents would be a lie
# that teaches the user to click through. Without one, the session really does end, and the
# warning is the only thing standing between a restart and a lost fan-out.
from asset_studio.routers import system as _sys        # noqa: E402
from asset_studio import cc_session as _cc             # noqa: E402
from asset_studio import subagents as _sa              # noqa: E402


class _K:
    def __init__(self, inbox):
        self.inbox = inbox


_real_alive = _cc.keeper_alive
_real_open = _sa.open_background
_saved = dict(_cc._live)
try:
    _cc._live.clear()
    _cc._live["kept"] = _K("C:/inbox-kept")
    _cc._live["bare"] = _K("")
    _cc.keeper_alive = lambda inbox: inbox == "C:/inbox-kept"
    _sa.open_background = lambda pid, window=1800.0: [
        {"agent_id": "a1", "description": "Build the boss fight", "background": True}]

    st = {"statuses": {"kept": True, "bare": True}}
    stranded = _sys._stranded_by_restart(st)
    projects = [x["project"] for x in stranded]
    check("a session with a keeper is NOT reported as at risk", "kept" not in projects, projects)
    check("a session without one IS", "bare" in projects, projects)
    check("and it names the agent",
          any("boss fight" in x["agent"] for x in stranded), stranded)

    bare = _sys._sessions_without_a_keeper(st)
    check("the sessions a restart would end are listed by name", bare == ["bare"], bare)

    _cc.keeper_alive = lambda inbox: True
    check("with every session kept, a restart strands nothing",
          _sys._stranded_by_restart(st) == [], _sys._stranded_by_restart(st))
    check("...and nothing is reported as unkeepered",
          _sys._sessions_without_a_keeper(st) == [], _sys._sessions_without_a_keeper(st))
finally:
    _cc.keeper_alive = _real_alive
    _sa.open_background = _real_open
    _cc._live.clear()
    _cc._live.update(_saved)

print("\n  %d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

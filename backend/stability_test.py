# -*- coding: utf-8 -*-
"""Stability: the backend must not be killed for being busy, and must not grow without bound.

What happened (2026-10-09, read off the Windows event logs): the D: NVMe drive the app lives on
dropped off the bus (a known WD SN770 2TB firmware fault under Windows 11 24H2+), python.exe and
electron.exe crashed together with in-page errors, and the shell respawned the backend every ~7 s
into the same missing disk. Separately, Windows flagged codex.exe — the app-server the Studio keeps
alive forever — for a memory leak. The fixes checked here are the software half:

  * /api/health runs on the event loop, so a full worker pool cannot make it miss the shell's probe
  * the shell waits 20 s (not 6) before calling the backend hung, probes with a 2.5 s timeout, kills
    the process that really holds the port, backs off when a backend keeps dying at start, and logs
    every kill/exit/respawn with its reason
  * an idle Codex app-server that has grown past the limit is recycled

Run:  backend/.venv/Scripts/python.exe backend/stability_test.py
"""
import asyncio
import inspect
import os
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["ASSET_STUDIO_DATA"] = tempfile.mkdtemp(prefix="stability-test-")

from asset_studio import codex_app, main  # noqa: E402
from asset_studio.config import settings  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print("  PASS  " + name)
    else:
        failed += 1
        print("  FAIL  " + name + (("  -- " + str(detail)[:300]) if detail else ""))


print("health")
check("/api/health is async (answers from the event loop, not the worker pool)",
      inspect.iscoroutinefunction(main.health))
r = asyncio.run(main.health())
check("...and still names the serving pid", r.get("ok") and r.get("pid") == os.getpid(), r)

print("desktop shell")
shell = (Path(__file__).resolve().parent.parent / "frontend" / "electron" / "main.cjs").read_text(encoding="utf-8")
check("the probe allows 2.5 s", "r.setTimeout(2500" in shell)
check("the watchdog waits 20 s before calling it hung", "downSince >= 20000" in shell)
wd = shell[shell.index("function startWatchdog"):shell.index("async function ensureBackend")]
wd = re.sub(r"//[^\n]*", "", wd)          # the code, not the comments that explain it
check("the watchdog kills the process that holds the port (killBackend), not only the stub",
      "killBackend();" in wd and "backendProc.kill()" not in wd)
check("a backend that keeps dying at start backs off, up to a minute",
      "quickDeaths" in shell and "Math.min(60000" in shell)
check("every exit and every watchdog kill is logged with its reason",
      shell.count("shellLog(") >= 4 and "data\", \"logs\"" in shell)

print("codex app-server recycling")


class _Srv:
    def __init__(self):
        self.pid = 4242
        self.stopped = False
        self.loaded = set()

    def alive(self):
        return not self.stopped

    def stop(self):
        self.stopped = True


srv = _Srv()
codex_app._recycler["idle_since"] = 0.0
with patch.object(codex_app, "_SRV", srv), patch.object(codex_app, "_tree_mb", return_value=3000.0), \
        patch.object(codex_app, "_busy", return_value=False):
    t = 1_000_000.0
    check("the first idle look only starts the clock", codex_app.recycle_if_bloated(now=t) is False)
    check("idle for 5 minutes is not long enough", codex_app.recycle_if_bloated(now=t + 300) is False)
    check("idle for 10 minutes and over the limit: recycled", codex_app.recycle_if_bloated(now=t + 601) is True
          and srv.stopped)

srv = _Srv()
codex_app._recycler["idle_since"] = 0.0
with patch.object(codex_app, "_SRV", srv), patch.object(codex_app, "_tree_mb", return_value=3000.0), \
        patch.object(codex_app, "_busy", return_value=True):
    codex_app.recycle_if_bloated(now=1.0)
    codex_app.recycle_if_bloated(now=10_000.0)
    check("never while a Codex turn is running", not srv.stopped)

srv = _Srv()
codex_app._recycler["idle_since"] = 0.0
with patch.object(codex_app, "_SRV", srv), patch.object(codex_app, "_tree_mb", return_value=500.0), \
        patch.object(codex_app, "_busy", return_value=False):
    codex_app.recycle_if_bloated(now=1.0)
    check("never while it is under the limit", codex_app.recycle_if_bloated(now=10_000.0) is False and not srv.stopped)

srv = _Srv()
codex_app._recycler["idle_since"] = 0.0
settings.update({"codex_recycle_mb": 0})
with patch.object(codex_app, "_SRV", srv), patch.object(codex_app, "_tree_mb", return_value=9000.0), \
        patch.object(codex_app, "_busy", return_value=False):
    codex_app.recycle_if_bloated(now=1.0)
    check("codex_recycle_mb = 0 switches it off", codex_app.recycle_if_bloated(now=10_000.0) is False)
settings.update({"codex_recycle_mb": 2048})
check("the tree size of a real process is measured", codex_app._tree_mb(os.getpid()) > 1.0)

print("single-flight (coalesce.py)")
import threading  # noqa: E402
import time  # noqa: E402

from asset_studio.coalesce import coalesced  # noqa: E402

calls = []
gate = threading.Event()


@coalesced(ttl=0)
def slow(x, k=""):
    calls.append((x, k))
    gate.wait(5)
    return {"x": x, "n": len(calls)}


out = []
ts = [threading.Thread(target=lambda: out.append(slow(1, k="a"))) for _ in range(20)]
for t in ts:
    t.start()
time.sleep(0.3)
gate.set()
for t in ts:
    t.join(5)
check("20 identical calls at once compute once", len(calls) == 1 and len(out) == 20, (len(calls), len(out)))
check("...and every caller gets the answer", all(o == {"x": 1, "n": 1} for o in out), out[:2])
out[0]["mutated"] = True
check("...as its own copy", "mutated" not in out[1])
calls.clear()
slow(1, k="a")
slow(1, k="a")
check("ttl 0: calls that do not overlap compute again (never a stale answer)", len(calls) == 2)
check("...and finished results are not kept in memory", not slow.coalesce_slots)
calls.clear()
slow(1, k="a")
slow(2, k="a")
slow(1, k="b")
check("different arguments are separate computations", len(calls) == 3)

boom_gate = threading.Event()
boom_calls = []


@coalesced(ttl=0)
def boom():
    boom_calls.append(1)
    boom_gate.wait(5)
    raise ValueError("broken")


errs = []


def run_boom():
    try:
        boom()
    except ValueError as e:
        errs.append(str(e))


ts = [threading.Thread(target=run_boom) for _ in range(3)]
for t in ts:
    t.start()
time.sleep(0.3)
boom_gate.set()
for t in ts:
    t.join(5)
check("an error reaches the caller (waiters retry, and get it too)", len(errs) == 3, errs)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)

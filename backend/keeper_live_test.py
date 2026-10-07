"""THE REAL THING: a real session, a real background agent, and the backend dying under it.

keeper_test.py proves the plumbing against a stand-in child. This proves the promise itself, with
the actual CLI and an actual delegated agent:

  1. A process standing in for the backend starts a session through the real `_spawn_live`, sends
     a prompt that DELEGATES a job to a background agent, and then exits — which is exactly what a
     backend restart does to the pipe it owns.
  2. The session is still there afterwards.
  3. The agent finishes its job and writes its file, from inside a process whose backend is gone.
  4. A different process adopts the session and talks to it — no respawn, so nothing was killed to
     make the conversation continue.

It costs a few small turns and takes a couple of minutes. It is not run with the other suites;
run it when the session machinery changes.

    python keeper_live_test.py

It never calls `adopt_orphans`, on purpose. That would sweep every session on the machine,
including the user's own live chat, and this file must not be able to end somebody's work.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import cc_session as cc      # noqa: E402

PROJECT = "keeper-live-proof"
WORK = Path(cc.DATA_DIR) / "tmp" / "keeper_live"
TARGET = WORK / "agent-was-here.txt"
BACKEND = str(Path(__file__).resolve().parent)

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, extra))


def wait_for(fn, secs, step=0.5, note=""):
    end = time.time() + secs
    while time.time() < end:
        if fn():
            return True
        time.sleep(step)
    return False


PROMPT = ("Use the Agent tool to delegate this, in the background — do NOT do it yourself. "
          "Tell the agent to create the file agent-was-here.txt in the current folder, "
          "containing exactly the word DONE. Then reply with the single word DELEGATED.")

# The stand-in backend. It starts the session the real way, sends the prompt, waits only long
# enough for the delegation to be dispatched, and dies.
LAUNCH = WORK / "_launch.py"


def phase_one():
    LAUNCH.parent.mkdir(parents=True, exist_ok=True)
    LAUNCH.write_text(
        "import json, sys, time\n"
        "sys.path.insert(0, %r)\n"
        "from asset_studio import cc_session as cc\n"
        "exe = cc.find_claude()\n"
        "args = cc._claude_stream_args(exe, None, 'sonnet', 'bypassPermissions', False,\n"
        "                              'default', %r, %r)\n"
        "live = cc._spawn_live(%r, args, %r, (exe, %r, 'sonnet'))\n"
        "cc._write_msg(live, %r)\n"
        "print(json.dumps({'cli': live.proc.pid, 'keeper': live.keeper_pid,\n"
        "                  'inbox': live.inbox, 'log': str(live.log_path)}), flush=True)\n"
        "# Stay only until the delegation has gone out, then die like a restarting backend.\n"
        "end = time.time() + 240\n"
        "while time.time() < end:\n"
        "    try:\n"
        "        blob = open(str(live.log_path), 'rb').read()\n"
        "    except OSError:\n"
        "        blob = b''\n"
        "    if b'DELEGATED' in blob or b'\"subtype\":\"success\"' in blob:\n"
        "        break\n"
        "    time.sleep(1.0)\n"
        "cc._reg_checkpoint(live)\n"
        % (BACKEND, PROJECT, str(WORK), PROJECT, str(WORK), str(WORK), PROMPT),
        encoding="utf-8")
    r = subprocess.run([sys.executable, "-u", str(LAUNCH)], cwd=BACKEND,
                       capture_output=True, text=True, timeout=420)
    line = [ln for ln in (r.stdout or "").splitlines() if ln.startswith("{")]
    if not line:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        sys.exit("the stand-in backend never started a session")
    return json.loads(line[0])


shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(parents=True, exist_ok=True)

print("A backend starts a session, delegates a job, and dies")
t0 = time.time()
info = phase_one()
print("  (the stand-in backend ran for %.0fs and has now exited)" % (time.time() - t0))
check("a session was started", info["cli"] > 0, info)
check("a keeper holds its stdin", info["keeper"] > 0, info)

try:
    print("\nThe session outlives the backend that started it")
    time.sleep(3.0)
    check("the CLI is still running", cc._pid_alive(info["cli"]), info["cli"])
    check("the keeper is still running", cc._pid_alive(info["keeper"]), info["keeper"])
    check("and it still answers a backend that asks", cc.keeper_alive(info["inbox"]))

    print("\nThe background agent finishes the job anyway")
    got = wait_for(lambda: TARGET.exists(), 420)
    check("the delegated file was written, by an agent whose backend is gone", got,
          "not found: %s" % TARGET)
    if got:
        check("...with the content it was asked for",
              TARGET.read_text(encoding="utf-8", errors="replace").strip().upper().startswith("DONE"),
              TARGET.read_text(encoding="utf-8", errors="replace")[:80])

    print("\nA DIFFERENT backend adopts it and talks to it, without a respawn")
    rec = json.loads(cc._reg_path(PROJECT).read_text(encoding="utf-8"))
    check("the registry knows where to write", rec.get("inbox") == info["inbox"], rec.get("inbox"))
    check("adoption succeeds", cc._adopt_one(rec) is True)
    live = cc._live.get(PROJECT)
    check("the adopted session is writable", bool(live and live.inbox), getattr(live, "inbox", ""))
    before = Path(info["log"]).stat().st_size
    check("a message goes through", cc._write_msg(live, "Reply with the single word: ALIVE"))
    check("the same process answers it",
          wait_for(lambda: b"ALIVE" in Path(info["log"]).read_bytes()[before:], 180),
          Path(info["log"]).read_bytes()[-300:])
    check("and its pid never changed -- nothing was restarted to make that work",
          cc._pid_alive(info["cli"]), info["cli"])
finally:
    cc._live.pop(PROJECT, None)
    for pid in (info.get("cli"), info.get("keeper")):
        if pid and cc._pid_alive(int(pid)):
            cc._force_kill_tree(int(pid))
    cc._stop_keeper(info.get("inbox") or "")
    try:
        cc._reg_path(PROJECT).unlink()
    except OSError:
        pass

print("\n  %d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

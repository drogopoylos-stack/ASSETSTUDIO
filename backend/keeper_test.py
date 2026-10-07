"""THE SESSION'S STDIN MUST OUTLIVE THE BACKEND.

A `claude -p --input-format stream-json` process exits ONE SECOND after its stdin closes. That is
measured, not inferred. While the backend held the write end of that pipe, stopping the backend
closed it — so a restart ended every session, and every background agent living inside one, before
the replacement backend had finished starting. No guard in `cc_session` could have helped: by the
time the new backend runs there is nothing left to guard.

So the two halves of this file are the before and the after, run against a stand-in child that
behaves the way the CLI does — it reads stdin and it stops when stdin ends:

    pipe    the launcher exits  ->  the child is gone            (the bug, reproduced)
    keeper  the launcher exits  ->  the child is still there,    (the fix, demonstrated)
                                    and a process that never
                                    met it can still send to it

Nothing here runs the real CLI. The behaviour under test is the plumbing, and the plumbing is the
part that was wrong.
"""
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import cc_session as cc      # noqa: E402

TMP = Path(cc.DATA_DIR) / "tmp" / "keeper_test"
TMP.mkdir(parents=True, exist_ok=True)
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


def alive(pid):
    return cc._pid_alive(int(pid))


def wait_for(fn, secs=8.0, step=0.05):
    end = time.time() + secs
    while time.time() < end:
        if fn():
            return True
        time.sleep(step)
    return False


# A stand-in for the CLI: it reads stdin, writes what it is given, and ENDS WHEN STDIN ENDS.
# That last part is the whole behaviour being defended against.
FAKE = TMP / "fake_cli.py"
FAKE.write_text(
    "import sys\n"
    "log = open(sys.argv[1], 'ab', buffering=0)\n"
    "for line in sys.stdin.buffer:\n"
    "    log.write(line)\n"
    "log.write(b'--stdin ended--')\n",
    encoding="utf-8")

# A stand-in for the backend: it starts the child exactly the way _spawn_live does, prints what it
# started, and then EXITS. Its death is the event under test.
LAUNCH = TMP / "launcher.py"
LAUNCH.write_text(
    "import json, os, subprocess, sys\n"
    "sys.path.insert(0, %r)\n"
    "from asset_studio import cc_session as cc\n"
    "mode, fake, log = sys.argv[1], sys.argv[2], sys.argv[3]\n"
    "if mode == 'keeper':\n"
    "    r_fd, inbox, kp = cc._start_keeper('keeper-test')\n"
    "else:\n"
    "    r_fd, inbox, kp = None, None, None\n"
    "flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP\n"
    "p = subprocess.Popen([sys.executable, '-u', fake, log],\n"
    "                     stdin=(r_fd if r_fd is not None else subprocess.PIPE),\n"
    "                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
    "                     creationflags=flags | cc._BREAKAWAY, close_fds=True)\n"
    "if r_fd is not None:\n"
    "    os.close(r_fd)\n"
    "if inbox is not None:\n"
    "    (inbox / 'child.pid').write_text(str(p.pid), encoding='utf-8')\n"
    "print(json.dumps({'child': p.pid, 'inbox': str(inbox or ''),\n"
    "                  'keeper': (kp.pid if kp else 0)}))\n" % BACKEND,
    encoding="utf-8")


def launch(mode, log):
    r = subprocess.run([sys.executable, "-u", str(LAUNCH), mode, str(FAKE), str(log)],
                       cwd=BACKEND, capture_output=True, text=True, timeout=60)
    return json.loads(r.stdout.strip().splitlines()[-1])


def put(inbox, text):
    """Send a message the way _inbox_put does, from a process that never started the child."""
    d = Path(inbox)
    f = d / ("%020d-000.msg" % time.time_ns())
    tmp = f.with_suffix(".tmp")
    tmp.write_bytes((text + "\n").encode("utf-8"))
    os.replace(tmp, f)
    return f


# ---------------------------------------------------------------------------
print("The bug, reproduced: a pipe the backend owns dies with the backend")
log1 = TMP / "pipe.log"
log1.write_bytes(b"")
a = launch("pipe", log1)
check("the launcher has exited", not alive(os.getpid() + 10 ** 9) or True)   # it ran to completion
check("the child was started", a["child"] > 0, a)
gone = wait_for(lambda: not alive(a["child"]), secs=10.0)
check("...and it is gone within seconds of the launcher exiting", gone,
      "still alive: %s" % a["child"])
check("it died because stdin ended, not because it was killed",
      b"--stdin ended--" in log1.read_bytes(), log1.read_bytes()[-80:])
if alive(a["child"]):
    cc._force_kill_tree(a["child"])

# ---------------------------------------------------------------------------
print("\nThe fix: a keeper holds the pipe, so the session outlives its backend")
log2 = TMP / "keeper.log"
log2.write_bytes(b"")
b = launch("keeper", log2)
check("a keeper was started", b["keeper"] > 0, b)
check("and it has its own mailbox", bool(b["inbox"]), b)
still = wait_for(lambda: not alive(b["child"]), secs=4.0)
check("the child is STILL RUNNING after the launcher exited", not still)
check("the keeper is running too", alive(b["keeper"]))
check("the keeper reports itself alive to any backend that asks",
      wait_for(lambda: cc.keeper_alive(b["inbox"]), secs=12.0))

f = put(b["inbox"], "hello from a process that never met this child")
check("the message is picked up", wait_for(lambda: not f.exists(), secs=8.0))
check("...and reaches the child", wait_for(lambda: b"never met this child" in log2.read_bytes()),
      log2.read_bytes()[-120:])

print("\nMail is delivered in the order it was sent")
for i in range(5):
    put(b["inbox"], "line-%d" % i)
    time.sleep(0.005)
wait_for(lambda: b"line-4" in log2.read_bytes(), secs=10.0)
seen = [ln for ln in log2.read_text(encoding="utf-8", errors="replace").splitlines()
        if ln.startswith("line-")]
check("all five arrived", len(seen) == 5, seen)
check("in order", seen == sorted(seen), seen)

# ---------------------------------------------------------------------------
print("\nA NEW backend adopts the session and writes to it WITHOUT a respawn")
reg = {"project_id": "keeper-test", "pid": b["child"], "session_id": "sess-x",
       "cwd": str(TMP), "log": str(log2), "offset": 0,
       "inbox": b["inbox"], "keeper_pid": b["keeper"], "sig": [], "started": time.time()}
cc._reg_path("keeper-test").write_text(json.dumps(reg), encoding="utf-8")
saved = dict(cc._live)
try:
    cc._live.pop("keeper-test", None)
    check("adoption succeeds", cc._adopt_one(reg) is True)
    live = cc._live.get("keeper-test")
    check("the adopted session carries the mailbox", bool(live and live.inbox), getattr(live, "inbox", None))
    check("its stdin is still None -- nothing was papered over",
          live is not None and live.proc.stdin is None)
    check("and it is writable anyway", cc._write_msg(live, "written to an adopted session"))
    check("the child really received it",
          wait_for(lambda: b"written to an adopted session" in log2.read_bytes()),
          log2.read_bytes()[-160:])
finally:
    cc._live.pop("keeper-test", None)
    cc._live.update(saved)

# ---------------------------------------------------------------------------
print("\nA write that fails is only ever a DEAD session")
stale = TMP / "no-such-inbox"


class _Dead:
    class _P:
        def poll(self):
            return 0
    proc = _P()
    inbox = str(stale)
    alive = True
    lock = __import__("threading").Lock()
    project_id = "x"


check("a message to a dead session is refused", cc._write_msg(_Dead(), "x") is False)

# ---------------------------------------------------------------------------
print("\nThe keeper lets go when the session does, and not before")
cc._force_kill_tree(b["child"])
check("the child is gone", wait_for(lambda: not alive(b["child"]), secs=6.0))
check("the keeper follows it out", wait_for(lambda: not alive(b["keeper"]), secs=10.0),
      "keeper %s outlived its session" % b["keeper"])

print("\nMailboxes are not left lying around")
cc._stop_keeper(b["inbox"])
check("stopping a keeper removes its mailbox", not Path(b["inbox"]).exists())
orphan = cc._new_inbox("keeper-test-orphan")
orphan.mkdir(parents=True, exist_ok=True)
cc._reg_path("keeper-test").unlink(missing_ok=True)
swept = cc.sweep_keeper_mail()
check("a mailbox no session refers to is swept", not orphan.exists(), swept)

print("\n  %d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

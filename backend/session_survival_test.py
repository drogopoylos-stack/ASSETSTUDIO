"""THE BACKEND STOPPING MUST NEVER STOP A SESSION.

The CLI runs its background agents INSIDE the session process, so anything that ends that process
ends their work with it — and `_kill_live` is a tree kill. Two paths could do it on a restart and
both are covered here.

`shutdown_all` used to close the sessions that read idle. The reasoning was sound and the READING
was not: `_stream_busy` is false for a parent waiting on background agents, for a message queued
and not yet started, and for a freshly ADOPTED session that has just read its predecessor's final
`result` out of the log. Each wrong reading cost a real conversation, and when a supervisor loop
restarted the backend thirty-six times the reading only had to be wrong once.

`adopt_orphans` used to treat any live stream it could not match to a registry record as an
untracked duplicate and kill it. Losing a record is not the same as being a duplicate — a write
that did not land, or competing backends rewriting the registry during a restart, would end
somebody's running agents.

Nothing here starts a real session; `_live` is populated with stand-ins.
"""
import io
import sys

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import cc_session as cc      # noqa: E402
from asset_studio import subagents as sa       # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, extra))


class _Proc:
    def poll(self):
        return None


class _Live:
    def __init__(self, project_id, busy):
        self.alive = True
        self.proc = _Proc()
        self.turn_active = busy
        self.last_write = 1 if busy else 0
        self.last_result = 0 if busy else 1      # not busy => reads finished
        self.project_id = project_id
        self.read_from = 4096
        self.session_id = "sess-" + project_id
        self.logf = None


print("A backend that stops leaves every session running")
killed, checkpointed = [], []
real_kill, real_ckpt, real_unreg = cc._kill_live, cc._reg_checkpoint, cc._unregister
saved = dict(cc._live)
try:
    cc._kill_live = lambda live: killed.append(live.project_id)
    cc._reg_checkpoint = lambda live: checkpointed.append(live.project_id)
    cc._unregister = lambda pid: killed.append("unregistered:" + str(pid))
    cc._live.clear()
    cc._live["busy-project"] = _Live("busy-project", busy=True)
    cc._live["looks-idle-project"] = _Live("looks-idle-project", busy=False)

    # The idle-looking one is the whole point: it is the reading that was wrong.
    check("the idle-looking session really does read idle",
          cc._stream_busy(cc._live["looks-idle-project"]) is False)

    cc.shutdown_all()

    check("nothing is killed", killed == [], killed)
    check("the busy one is checkpointed", "busy-project" in checkpointed, checkpointed)
    check("and so is the one that looked idle", "looks-idle-project" in checkpointed, checkpointed)
    check("every session is checkpointed, so the next backend can resume it",
          len(checkpointed) == 2, checkpointed)
finally:
    cc._kill_live, cc._reg_checkpoint, cc._unregister = real_kill, real_ckpt, real_unreg
    cc._live.clear()
    cc._live.update(saved)

print("\nA session whose bookkeeping was lost is not a duplicate")
CMD = (r"c:\users\administrator\.local\bin\claude.EXE -p --input-format stream-json "
       r"--output-format stream-json --verbose --settings "
       r"C:\Users\Administrator\Downloads\STUDIO\data\btw\%s.settings.json --resume abc")

check("the project is read off the command line",
      cc._project_of_cmdline(CMD % "c--Users-Downloads-STUDIO") == "c--Users-Downloads-STUDIO",
      cc._project_of_cmdline(CMD % "c--Users-Downloads-STUDIO"))
check("a command line without one gives nothing", cc._project_of_cmdline("claude --help") == "")

known = {"pid": 100, "cmdline": CMD % "proj-a", "marker": True}
dupe = {"pid": 101, "cmdline": CMD % "proj-a", "marker": True}      # 2nd stream on proj-a
orphan = {"pid": 102, "cmdline": CMD % "proj-b", "marker": True}    # record lost, not a duplicate
reg = [{"pid": 100, "project_id": "proj-a"}]

adopt, kill = cc._classify_orphans([known, dupe, orphan], reg)
check("the registered one is adopted", [p["pid"] for p, _ in adopt] == [100],
      [p["pid"] for p, _ in adopt])
check("a SECOND stream on an adopted project is killed", [p["pid"] for p in kill] == [101],
      [p["pid"] for p in kill])
check("a session we merely lost track of is left running", 102 not in [p["pid"] for p in kill],
      [p["pid"] for p in kill])
check("...and is not silently adopted either", 102 not in [p["pid"] for p, _ in adopt])

adopt2, kill2 = cc._classify_orphans([orphan], [])
check("with an empty registry nothing is killed", kill2 == [], kill2)

print("\nThe reaper's guard covers the whole period it guards")
import inspect                                        # noqa: E402

sig = inspect.signature(cc._has_recent_agent_activity)
check("the agent-activity window matches the idle timeout",
      sig.parameters["window"].default == cc._IDLE_TIMEOUT,
      "%s vs %s" % (sig.parameters["window"].default, cc._IDLE_TIMEOUT))

body = open("asset_studio/cc_session.py", encoding="utf-8").read()
after = body.split("def shutdown_all", 1)[1].split("\ndef ", 1)[0]
# The name appears — the docstring explains why the kill was removed. What must not exist is a CALL.
check("shutdown_all never calls _kill_live", "_kill_live(" not in after)

print("\n/compact must not interrupt a background agent")
# Compaction is not quiet bookkeeping: the CLI stops what it is doing to make room, and a
# background agent dispatched in that turn is interrupted with it. An interrupt leaves no
# transcript marker, so no completion record is ever written and the next session can only
# report "stopped, no completion record found". That is exactly what happened: two agents ending
# `[Request interrupted by user]` in the same second, one of them 109 tool calls in.
check("a /compact is recognised however it is typed",
      cc._is_compact_cmd("/compact") and cc._is_compact_cmd("  /COMPACT keep the plan "))
check("an ordinary message is not", not cc._is_compact_cmd("compact the shop boards"))

real_open = sa.open_background
try:
    sa.open_background = lambda pid, window=1800.0: [
        {"agent_id": "a1", "description": "Fix the Tsunami texture", "background": True},
        {"agent_id": "a2", "description": "Carry models and dino evolution", "background": True},
    ]
    r = cc._send_streaming("p", "/compact", "C:\\nowhere", "", "", False, "", "claude.exe",
                           False, "", None)
    check("the send is refused", r.get("ok") is False, r)
    check("it says how many are at risk", "2 background agents" in (r.get("error") or ""),
          r.get("error"))
    check("and names them", "Fix the Tsunami texture" in (r.get("error") or ""), r.get("error"))
    check("it refuses BEFORE anything is spawned", "p" not in cc._live, sorted(cc._live))

    sa.open_background = lambda pid, window=1800.0: []
    r2 = cc._send_streaming("p", "/compact", "C:\\nowhere", "", "", False, "", "claude.exe",
                            False, "", None)
    check("with nothing running it is NOT refused by this guard",
          "background agent" not in (r2.get("error") or ""), r2.get("error"))
finally:
    sa.open_background = real_open
    cc._live.pop("p", None)

print("\nThe question is asked generously")
import inspect as _inspect                                     # noqa: E402

check("open_background looks back much further than the 90s running window",
      _inspect.signature(sa.open_background).parameters["window"].default > sa.RUNNING_WINDOW * 10,
      _inspect.signature(sa.open_background).parameters["window"].default)
check("and it only counts BACKGROUND agents",
      "background" in _inspect.getsource(sa.open_background))

print("\nA SESSION RESPAWN MUST NOT KILL A BACKGROUND AGENT")
# A model switch, a settings change, a peer engine on the same transcripts, the idle reaper, and
# an edit-rewind all restart the chat process — and a background agent lives INSIDE that process,
# so each of these could orphan it. A pair of agents died exactly this way on a model switch. The
# guard is one authoritative signal, `_open_background`, wired into every involuntary path.


class _StubLive:
    """Stands in for a live session; records whether it was killed."""
    def __init__(self, project_id, sig):
        self.project_id = project_id
        self.sig = sig
        self.session_id = "sess-1"
        self.last_write = __import__("time").time()
        self.last_result = 0.0        # _stream_busy compares last_write > last_result
        self.turn_active = False
        self.killed = False

    class _P:
        def poll(self):
            return None
    proc = _P()
    alive = True


import time as _time                                  # noqa: E402

# `_open_background` is the single source of truth. Force it to report one open agent, the way the
# real one does when subagents.open_background finds a dispatched-but-unreported task.
_real_open_bg = cc._open_background
_real_kill = cc._kill_live
_real_recent = cc._has_recent_agent_activity
PID = "proj-guard-test"
try:
    cc._open_background = lambda pid: [{"agent_id": "bg1", "description": "Build the thing"}]

    check("the guard reports the open agent", bool(cc._open_background(PID)))

    # 1) The idle reaper must skip a process whose only sign of life is a background agent.
    killed = {"n": 0}
    cc._kill_live = lambda live: killed.__setitem__("n", killed["n"] + 1)
    stub = _StubLive(PID, ("exe", "cwd", "opus"))
    cc._live[PID] = stub
    stub.last_write = _time.time() - (cc._IDLE_TIMEOUT + 60)   # 15+ min idle: reaper would fire
    stub.last_result = stub.last_write                          # not mid-turn, so _stream_busy False
    # Run one reaper pass inline (the loop body), rather than sleeping 60s for the thread.
    now = _time.time()
    dead = (not stub.alive) or stub.proc.poll() is not None
    idle = (not cc._stream_busy(stub)) and (now - stub.last_write) > cc._IDLE_TIMEOUT
    reaped = False
    if dead or idle:
        if idle and not dead and cc._open_background(stub.project_id):
            reaped = False
        else:
            reaped = True
    check("the idle reaper spares a session with an open agent", reaped is False)
    check("and it did not kill it", killed["n"] == 0, killed["n"])

    # 2) A peer engine on the same transcripts is not silently killed while it has an open agent.
    src = _inspect.getsource(cc._stop_shared)
    check("_stop_shared consults the guard before killing a peer",
          "_open_background(other)" in src, src[-400:])

    # 3) The sig/model-change respawn defers while an agent is open — the clause is guarded.
    src2 = _inspect.getsource(cc._send_streaming)
    check("the sig-change respawn is gated on the guard",
          "not _open_background(project_id)" in src2)

    # 4) Edit-rewind refuses rather than restarting the session under a live agent.
    src3 = _inspect.getsource(cc.rewind)
    check("rewind refuses while a background agent is open",
          "_agents_block(project_id" in src3)
    check("...and every refusal is worded in ONE place",
          "still working" in _inspect.getsource(cc._agents_block))
finally:
    cc._open_background = _real_open_bg
    cc._kill_live = _real_kill
    cc._has_recent_agent_activity = _real_recent
    cc._live.pop(PID, None)

# The fallback path: even with no recorded notification, a fresh journal keeps a session alive.
try:
    cc._has_recent_agent_activity = lambda pid, window=None: True
    # subagents.open_background may return [] here; the mtime fallback must still protect.
    got = cc._open_background("nonexistent-project-xyz")
    check("a fresh fan-out journal alone is enough to protect a session", bool(got), got)
finally:
    cc._has_recent_agent_activity = _real_recent


# ---------------------------------------------------------------------------
print("\nA REFUSAL AND A REAP ASK DIFFERENT QUESTIONS")
# Being wrong costs opposite things. An involuntary stop must be prevented on the faintest sign of
# life, so `_open_background` counts journal mtimes as well. A refusal shown to the user must not
# fire because an agent finished four minutes ago and its file is still warm, so `_owed_background`
# asks only the question with an outcome in it. Same fact, two thresholds, on purpose.
_r_recent = cc._has_recent_agent_activity
_r_open = sa.open_background
try:
    cc._has_recent_agent_activity = lambda pid, window=None: True
    sa.open_background = lambda pid, window=1800.0: []
    check("a warm journal alone still protects against an involuntary stop",
          bool(cc._open_background("warm-project")))
    check("...but does NOT produce a refusal", cc._owed_background("warm-project") == [])
    check("and so nothing is refused", cc._agents_block("warm-project", "This") == "")
finally:
    cc._has_recent_agent_activity = _r_recent
    sa.open_background = _r_open


print("\nEVERY DOOR THAT RESTARTS A SESSION IS GUARDED")


class _Stub:
    """A live session that is mid-turn and whose stdin cannot be reached — an inherited one."""
    class _P:
        pid = 4242
        stdin = None

        def poll(self):
            return None
    proc = _P()

    def __init__(self, project_id="p"):
        import threading as _t
        self.project_id = project_id
        self.session_id = "sess-1"
        self.sig = ()
        self.inbox = ""
        self.alive = True
        self.lock = _t.Lock()
        self.outstanding = 0
        self.turn_active = False
        self.last_write = __import__("time").time()
        self.last_result = 0.0        # last_write > last_result => busy, so no sig respawn
        self.btw_pending = False


_k_force, _k_live, _r_open2 = cc._force_kill_tree, cc._kill_live, sa.open_background
killed = []
try:
    sa.open_background = lambda pid, window=1800.0: [
        {"agent_id": "a1", "description": "Carry the dino evolution", "background": True}]
    cc._force_kill_tree = lambda pid: killed.append(("force", pid))
    cc._kill_live = lambda live: killed.append(("live", getattr(live, "project_id", "?")))

    cc._live["p"] = _Stub()
    args = ("p", "hello", "C:\\nowhere", "", "", False, "", "claude.exe")

    r = cc._send_streaming(*args, True, "", None)                  # new conversation
    check("starting a NEW conversation is refused", r.get("ok") is False, r)
    check("...and names the agent", "dino evolution" in (r.get("error") or ""), r.get("error"))

    r = cc._send_streaming(*args, False, "another-session", None)  # switch conversation
    check("switching to another conversation is refused", r.get("ok") is False, r)

    r = cc._send_streaming(*args, False, "", None)                 # ordinary send, write fails
    check("a send whose write fails is refused, not forced", r.get("ok") is False, r)
    check("NOTHING WAS KILLED on any of the three", killed == [], killed)

    sa.open_background = lambda pid, window=1800.0: []
    cc._live["p"] = _Stub()
    r = cc._send_streaming(*args, False, "", None)
    check("with no agent at risk the same send is NOT refused by this guard",
          "still working" not in (r.get("error") or ""), r.get("error"))
finally:
    cc._force_kill_tree, cc._kill_live, sa.open_background = _k_force, _k_live, _r_open2
    cc._live.pop("p", None)

src_t = _inspect.getsource(cc.become_terminal)
check("handing the conversation to a terminal is guarded too", "_agents_block(" in src_t)
try:
    from asset_studio import workspace as _ws
    # rename_root, not rename: only renaming a WORKSPACE kills its session (its cwd moves).
    check("renaming a workspace is guarded too",
          "_agents_block(" in _inspect.getsource(_ws.rename_root))
except Exception as e:                                              # pragma: no cover
    check("renaming a project is guarded too", False, e)


print("\nSTDIN IS HELD OUTSIDE THE BACKEND")
# The measured fact this rests on: a stream-json session exits ONE SECOND after its stdin closes.
# keeper_test.py demonstrates the before and the after against a stand-in child; here we only
# check that the session path actually uses it.
src_spawn = _inspect.getsource(cc._spawn_live)
check("a session is started behind a keeper", "_start_keeper(project_id)" in src_spawn)
check("the CLI reads the keeper's pipe, not one of ours", "stdin=(r_fd if r_fd" in src_spawn)
check("an adopted session is handed the mailbox",
      'rec.get("inbox")' in _inspect.getsource(cc._adopt_one))
check("the registry carries it so a LATER backend can find it",
      '"inbox": live.inbox' in _inspect.getsource(cc._register))
check("a slow keeper is never mistaken for a dead session",
      "the message stays queued" in _inspect.getsource(cc._inbox_put))


print("\nHOLDING STDIN OPEN MUST NOT LEAK SESSIONS")
_our = cc._our_live_processes
_reg = cc._read_registry
_kf = cc._force_kill_tree
_ob = cc._open_background
stopped = []
try:
    cc._force_kill_tree = lambda pid: stopped.append(pid)
    cc._read_registry = lambda: [{"pid": 200, "project_id": "claimed"}]
    cc._our_live_processes = lambda: [
        {"pid": 200, "cmdline": CMD % "claimed", "marker": True},
        {"pid": 201, "cmdline": CMD % "busy-with-agents", "marker": True},
        {"pid": 202, "cmdline": CMD % "nobody-waiting", "marker": True},
    ]
    cc._open_background = lambda pid: [{"agent_id": "x"}] if pid == "busy-with-agents" else []
    # The sweep needs POSITIVE evidence that a session is finished. Give 202 a stale log so it
    # has some; the others are protected by the checks above it.
    stale = cc.SESS_DIR / "nobody-waiting.log"
    stale.write_bytes(b"")
    import os as _os
    old_t = __import__("time").time() - (cc._IDLE_TIMEOUT + 600)
    _os.utime(stale, (old_t, old_t))

    cc.sweep_untracked()
    check("a session another backend claims is left alone", 200 not in stopped, stopped)
    check("a session with an agent owed a result is left alone", 201 not in stopped, stopped)
    check("only the one nobody is waiting on is stopped", stopped == [202], stopped)

    # An unreadable or missing log is not evidence of anything. Falling through to the kill there
    # would have made a lost file a death sentence.
    stopped.clear()
    stale.unlink()
    cc.sweep_untracked()
    check("with no log to read, nothing is killed", stopped == [], stopped)

    stopped.clear()
    cc._our_live_processes = lambda: [{"pid": 303, "cmdline": "claude --stream-json", "marker": True}]
    cc.sweep_untracked()
    check("a process we cannot identify is left alone", stopped == [], stopped)
finally:
    cc._our_live_processes, cc._read_registry = _our, _reg
    cc._force_kill_tree, cc._open_background = _kf, _ob

print("\n  %d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

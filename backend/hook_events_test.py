"""Do the compaction and phase hooks record the right thing, and change the right reading?

Every payload here is the real shape the CLI sends, taken from the hook input the 2.1.257 binary
builds: the base object (session_id, transcript_path, cwd, permission_mode, agent_id, effort)
plus the per-event fields. The hook script is run as a real subprocess over stdin, exactly as the
CLI runs it, rather than being imported — so a crash in argument handling or encoding shows up
here instead of silently killing the record on a live session.
"""
import io
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import hook_events as he     # noqa: E402
from asset_studio import mission               # noqa: E402

PROJ = "hook-events-test"
LEDGER = he.file_for(PROJ)
LEDGER.parent.mkdir(parents=True, exist_ok=True)
LEDGER.unlink(missing_ok=True)
HOOK = Path("asset_studio/session_hook.py").resolve()
SESSION = "11111111-2222-3333-4444-555555555555"

ok = fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  " + label)
    else:
        fail += 1
        print("  FAIL  " + label + "   " + str(extra))


def fire(**fields):
    """Run the hook exactly as the CLI does: one JSON object on stdin, ledger path in argv."""
    payload = {"session_id": SESSION, "transcript_path": r"C:\x\y.jsonl", "cwd": r"C:\x",
               "permission_mode": "bypassPermissions", "effort": "high"}
    payload.update(fields)
    r = subprocess.run([sys.executable, str(HOOK), str(LEDGER)],
                       input=json.dumps(payload), capture_output=True, text=True)
    return r


def reset():
    LEDGER.unlink(missing_ok=True)
    he._cache.clear()


print("The hook writes one line per event, and ignores the rest")
fire(hook_event_name="PreCompact", trigger="manual", custom_instructions="")
fire(hook_event_name="PostCompact", trigger="manual", compact_summary="S" * 20000)
fire(hook_event_name="PreToolUse", tool_name="Bash")          # not ours
fire(hook_event_name="SessionEnd", reason="clear")            # not ours
he._cache.clear()
rows = he.events(PROJ)
check("both compaction events recorded", [r["event"] for r in rows] == ["PreCompact", "PostCompact"],
      [r["event"] for r in rows])
check("an event we did not ask for is dropped", len(rows) == 2)
check("a malformed payload cannot fail a turn", fire(hook_event_name="PostCompact").returncode == 0)

print()
print("Compaction state")
reset()
check("nothing recorded -> nothing claimed", he.compact_state(PROJ) == {})
fire(hook_event_name="PreCompact", trigger="auto")
he._cache.clear()
cs = he.compact_state(PROJ)
check("a PreCompact with no PostCompact reads as running", cs["running"] is True, cs)
check("...and says the window filled up rather than the user asking", cs["trigger"] == "auto")
fire(hook_event_name="PostCompact", trigger="auto", compact_summary="S" * 20000)
he._cache.clear()
cs = he.compact_state(PROJ)
check("PostCompact ends it", cs["running"] is False)
check("the summary is measured, not assumed", cs["summary_tokens"] == 5000, cs.get("summary_tokens"))

reset()
fire(hook_event_name="PreCompact", trigger="manual")
he._cache.clear()
rows = he.events(PROJ)
rows[0]["at"] = time.time() - he.COMPACT_STALE - 1     # a compaction that never reported back
check("a compaction that never finished stops claiming to be running",
      he.compact_state(PROJ)["running"] is False)

print()
print("The meter is corrected during the gap before the transcript catches up")
reset()
fire(hook_event_name="PreCompact", trigger="auto")
fire(hook_event_name="PostCompact", trigger="auto", compact_summary="S" * 20000)
he._cache.clear()
# What the meter sees in the gap: the newest assistant turn is still the PRE-compact one.
stale = {"model": "claude-opus-5[1m]", "ctx_used": 940_000, "ctx_max": 1_000_000,
         "ctx_pct": 94.0, "ctx_remaining": 0.0, "just_compacted": False}
info = dict(stale)
mission._apply_compact_hook(info, PROJ)
check("the stale high-water mark is replaced", info["ctx_used"] == 30_000, info["ctx_used"])
check("...with base + the real summary (25000 + 5000)", info["ctx_used"] == 25_000 + 5_000)
check("the bar drops with it", info["ctx_pct"] == 3.0, info["ctx_pct"])
check("headroom is recomputed, not left at zero", info["ctx_remaining"] > 90, info["ctx_remaining"])
check("it is flagged as an estimate", info["just_compacted"] is True)
check("the trigger is passed through", info["compact_trigger"] == "auto")

print()
print("...and never makes a real reading worse")
info = dict(stale, ctx_used=12_000, ctx_pct=1.2)     # a real turn already landed, small context
mission._apply_compact_hook(info, PROJ)
check("a figure already lower than the estimate is left alone", info["ctx_used"] == 12_000)
info = dict(stale, just_compacted=True)
mission._apply_compact_hook(info, PROJ)
check("the transcript's own estimate wins once it exists", info["ctx_used"] == 940_000)
he._cache.clear()
rows = he.events(PROJ)
for r in rows:
    r["at"] = time.time() - he._COMPACT_GAP - 1 if hasattr(he, "_COMPACT_GAP") else r["at"]
info = dict(stale)
old = mission._COMPACT_GAP
mission._COMPACT_GAP = -1                             # pretend the gap has long passed
mission._apply_compact_hook(info, PROJ)
mission._COMPACT_GAP = old
check("once the gap has passed it stops correcting", info["ctx_used"] == 940_000)

print()
print("Phase runs come from creations, which are never rewritten")
# Written straight to the ledger rather than through the hook, because these need timestamps a
# week apart. The read path is the real one — `events()` re-validates its cache against the
# file, so poking the cache would be ignored, which is what a first version of this test did.
def ledger(rows):
    he._cache.clear()
    LEDGER.write_text("".join(json.dumps(r) + chr(10) for r in rows), encoding="utf-8")


def made(at, tid, agent=""):
    return {"at": at, "event": "TaskCreated", "session": SESSION, "agent": agent,
            "task_id": str(tid), "subject": "phase %s" % tid, "description": ""}


now = time.time()
ledger([made(now - 7 * 86400, i) for i in (1, 2, 3)] + [made(now, i) for i in (4, 5, 6)])
runs = he.task_runs(PROJ)
check("two runs, not one welded list", len(runs) == 2, len(runs))
check("the newest run holds only tonight's phases", runs[-1]["ids"] == ["4", "5", "6"], runs[-1]["ids"])
check("last week's run is kept as history", runs[0]["ids"] == ["1", "2", "3"])
check("latest_run_ids agrees", he.latest_run_ids(PROJ) == {"4", "5", "6"})

# The gap is what separates them, and one turn writes its whole list in seconds.
ledger([made(now, 1), made(now + 2, 2), made(now + 4, 3)])
check("phases written seconds apart are ONE run", len(he.task_runs(PROJ)) == 1)
ledger([made(now, 1), made(now + he.RUN_GAP + 1, 2)])
check("a gap longer than RUN_GAP splits them", len(he.task_runs(PROJ)) == 2)

print()
print("A run reports as finished only when every phase in it did")
ledger([made(now, 1), made(now, 2),
        {"at": now + 9, "event": "TaskCompleted", "session": SESSION, "agent": "",
         "task_id": "1", "subject": "phase 1"}])
check("one of two done -> still open", he.task_runs(PROJ)[-1]["ended"] == 0.0)
ledger([made(now, 1), made(now, 2),
        {"at": now + 9, "event": "TaskCompleted", "session": SESSION, "agent": "",
         "task_id": "1", "subject": "phase 1"},
        {"at": now + 11, "event": "TaskCompleted", "session": SESSION, "agent": "",
         "task_id": "2", "subject": "phase 2"}])
check("both done -> the run has an end time", he.task_runs(PROJ)[-1]["ended"] == now + 11)

print()
print("A subagent's own phases are not mixed into the session's")
ledger([made(now, 4), made(now, 5), made(now, 6), made(now, 99, agent="a1b2c3")])
check("the agent's phase is left out", he.latest_run_ids(PROJ) == {"4", "5", "6"})

print()
print("Instructions loaded — the part of a window nobody chose")
reset()
big = Path("_ins_big.md"); big.write_text("x" * 8000, encoding="utf-8")
small = Path("_ins_small.md"); small.write_text("y" * 400, encoding="utf-8")
fire(hook_event_name="InstructionsLoaded", file_path=str(big.resolve()),
     memory_type="project", load_reason="startup")
fire(hook_event_name="InstructionsLoaded", file_path=str(small.resolve()),
     memory_type="project", load_reason="import", parent_file_path=str(big.resolve()))
fire(hook_event_name="InstructionsLoaded", file_path=str(big.resolve()),
     memory_type="project", load_reason="startup")            # the same file again
he._cache.clear()
ins = he.instructions(PROJ)
check("each file counted once", ins["count"] == 2, ins["count"])
check("the weight is read off the file, not guessed", ins["tokens"] == 2000 + 100, ins["tokens"])
check("the heaviest is first", ins["files"][0]["name"] == "_ins_big.md")
check("an imported file names its importer", ins["files"][1]["parent"] == "_ins_big.md",
      ins["files"][1]["parent"])
check("the reason is kept", ins["files"][1]["reason"] == "import")

# A file loaded by an OLDER session is in a context that no longer exists.
rows = he.events(PROJ)
for r in rows[:2]:
    r["session"] = "an-older-session"
LEDGER.write_text("".join(json.dumps(r) + chr(10) for r in rows), encoding="utf-8")
he._cache.clear()
check("only the newest session's files are counted", he.instructions(PROJ)["count"] == 1,
      he.instructions(PROJ))
big.unlink(); small.unlink()

print()
print("The two Worktree events are not registered, and must not be")
from asset_studio import session_hook as sh
check("WorktreeCreate is not an event we record", "WorktreeCreate" not in sh._EVENTS)
check("WorktreeRemove is not an event we record", "WorktreeRemove" not in sh._EVENTS)
r = fire(hook_event_name="WorktreeCreate", name="feature-x")
check("...and firing one records nothing", r.returncode == 0 and not r.stdout.strip(), r.stdout[:80])

print()
print("Nothing recorded means nothing changes")
reset()
check("no ledger -> no ids, so the caller keeps its heuristic", he.latest_run_ids(PROJ) == set())
check("no ledger -> the meter is untouched", (lambda i: (mission._apply_compact_hook(i, PROJ), i)[1])
      (dict(stale))["ctx_used"] == 940_000)

LEDGER.unlink(missing_ok=True)
print("\n  %d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

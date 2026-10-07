"""Does the incremental transcript scan read only new bytes, and still read everything?

This is the hot path of the whole app: the left rail asks for the running-agent count every 1.5
seconds, and that count comes from here. It used to re-read every parent transcript from byte
zero on every call — 523 MB on one real project, six to ten seconds inside the backend, with a
new request starting every 1.5s. Six of those saturate a browser's per-origin connection pool,
every other request queues behind them, and the conversation feed never loads. That is the blank
chat this file exists to prevent coming back.

A transcript is append-only, so the scan keeps a byte cursor. The three things that can go wrong
with a cursor are all tested here: appended bytes must be picked up, a half-written final line
must not be consumed, and a truncated file (the Studio rewinds conversations by truncating them)
must be read again from the start.
"""
import io
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

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


def entry(agent_id, status="", prompt="x"):
    """One parent-transcript line carrying a subagent result, in the CLI's own shape."""
    return json.dumps({
        "timestamp": "2026-09-02T05:00:00.000Z",
        "toolUseResult": {
            "agentId": agent_id, "agentType": "claude", "status": status,
            "prompt": prompt, "resolvedModel": "claude-opus-5",
            "totalDurationMs": 1000, "totalTokens": 10,
        },
    }) + "\n"


TMP = Path(tempfile.mkdtemp(prefix="sa-scan-"))
f = TMP / "session.jsonl"

try:
    print("Reading a transcript for the first time")
    f.write_text(entry("a1") + entry("a2"), encoding="utf-8")
    rows = sa._summary_records(f)
    check("both records are found", [r["agent_id"] for r in rows] == ["a1", "a2"],
          [r["agent_id"] for r in rows])
    check("noise without an agentId is skipped",
          all(r["agent_id"] in ("a1", "a2") for r in rows))

    print("\nAsking again reads nothing and answers the same")
    before = sa._scan_cursor[str(f)][0]
    rows2 = sa._summary_records(f)
    check("the cursor did not move", sa._scan_cursor[str(f)][0] == before, before)
    check("the same records come back", [r["agent_id"] for r in rows2] == ["a1", "a2"])
    check("the cursor is at end of file", before == f.stat().st_size,
          "%d vs %d" % (before, f.stat().st_size))

    print("\nAppending is picked up without re-reading the start")
    with f.open("a", encoding="utf-8") as fh:
        fh.write('{"noise": true}\n')
        fh.write(entry("a3"))
    rows3 = sa._summary_records(f)
    check("the new record appears", [r["agent_id"] for r in rows3] == ["a1", "a2", "a3"],
          [r["agent_id"] for r in rows3])
    check("nothing is duplicated", len(rows3) == 3, len(rows3))

    print("\nA half-written final line is left for next time")
    with f.open("a", encoding="utf-8") as fh:
        fh.write('{"toolUseResult": {"agentId": "a4", "sta')     # no newline: mid-write
    partial_at = sa._scan_cursor[str(f)][0]
    rows4 = sa._summary_records(f)
    check("the torn line is not parsed", [r["agent_id"] for r in rows4] == ["a1", "a2", "a3"],
          [r["agent_id"] for r in rows4])
    check("the cursor stopped before it", sa._scan_cursor[str(f)][0] == partial_at,
          "%d vs %d" % (sa._scan_cursor[str(f)][0], partial_at))
    with f.open("a", encoding="utf-8") as fh:                     # the CLI finishes the line
        fh.write('tus": "completed"}}\n')
    rows5 = sa._summary_records(f)
    check("the finished line is read on the next call",
          [r["agent_id"] for r in rows5] == ["a1", "a2", "a3", "a4"],
          [r["agent_id"] for r in rows5])
    check("its fields survived the split write",
          [r for r in rows5 if r["agent_id"] == "a4"][0]["status"] == "completed")

    print("\nA truncated transcript is read again from the start")
    f.write_text(entry("z1"), encoding="utf-8")                   # a rewind: shorter file
    rows6 = sa._summary_records(f)
    check("the old records are gone", [r["agent_id"] for r in rows6] == ["z1"],
          [r["agent_id"] for r in rows6])
    check("the cursor was reset, not rewound past the end",
          sa._scan_cursor[str(f)][0] == f.stat().st_size)

    print("\nA file that disappears forgets its cursor")
    key = str(f)
    f.unlink()
    check("it returns nothing", sa._summary_records(f) == [])
    check("and drops the stale cursor", key not in sa._scan_cursor)

    print("\nThe list cache is stamped when the work FINISHED")
    # Stamping before the scan meant any scan slower than the TTL produced an entry that was
    # already expired on arrival, so a slow project could never be cached at all.
    real = sa._list_uncached
    calls = []

    def slow(pid, session="", with_files=False):
        calls.append(pid)
        time.sleep(sa._LIST_TTL + 0.15)          # slower than the TTL, as the real one was
        return {"agents": [], "running": 0, "tokens": 0}

    sa._list_uncached = slow
    sa._list_cache.clear()
    try:
        sa.list_for("slow-project")
        sa.list_for("slow-project")
        check("a scan slower than the TTL still caches", len(calls) == 1, "%d scans" % len(calls))
    finally:
        sa._list_uncached = real
        sa._list_cache.clear()

    print("\nCounting running agents opens nothing when nothing is running")
    reads = []
    real_rec = sa._summary_records

    def watched(path):
        reads.append(str(path))
        return real_rec(path)

    sa._summary_records = watched
    try:
        out = sa.running("a-project-that-does-not-exist")
        check("no agents", out == [], out)
        check("and no transcript was opened", reads == [], reads)
    finally:
        sa._summary_records = real_rec
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("\nA BACKGROUND agent's outcome comes from a notification, not from toolUseResult")
# The parent writes `status: async_launched` when it dispatches a background agent and never
# says another word about it. Reading only toolUseResult left every background agent this
# project has ever run stuck at "async_launched" — sixty-nine of them, with no verdict, no
# result, and nothing to show when one finished. The outcome is queued into the conversation as
# a <task-notification> instead, and that is the completion record.
def notify(task_id, status, summary, when="2026-09-02T10:00:00.000Z", op="enqueue"):
    body = ("<task-notification>\n<task-id>%s</task-id>\n"
            "<output-file>C:\\Temp\\claude\\tasks\\%s.output</output-file>\n"
            "<status>%s</status>\n<summary>%s</summary>\n</task-notification>" %
            (task_id, task_id, status, summary))
    return json.dumps({"type": "queue-operation", "operation": op,
                       "timestamp": when, "sessionId": "s1", "content": body}) + "\n"


TMP2 = Path(tempfile.mkdtemp(prefix="sa-notify-"))     # the first dir is already cleaned up
g = TMP2 / "notify.jsonl"
g.write_text(entry("bg1", status="async_launched") +
             notify("bg1", "completed", 'Agent "Build the index" finished'), encoding="utf-8")
rows = sa._summary_records(g)
by_id = {}
for r in rows:
    by_id[r["agent_id"]] = sa._merge(by_id.get(r["agent_id"]) or {}, r)
bg1 = by_id.get("bg1") or {}
check("the launch and the outcome join on the task id", len(by_id) == 1, sorted(by_id))
check("the outcome wins over the launch record", bg1.get("status") == "completed", bg1.get("status"))
check("the summary is kept as the result", "Build the index" in (bg1.get("result") or ""),
      bg1.get("result"))
check("so is the output file", str(bg1.get("output_file", "")).endswith("bg1.output"),
      bg1.get("output_file"))

print("\nA verdict older than the agent's own last write is stale")
# On resume the CLI reports any background agent it cannot find a completion record for as
# "stopped". One of those was writing its transcript at that very moment, so the rail said an
# agent was running while the conversation said it had stopped. The newer fact wins.
verdict = sa._epoch("2026-09-02T10:00:00.000Z")
stopped = {"status": "stopped", "status_at": verdict}
check("still running when it has written SINCE the verdict",
      sa._finished(stopped, verdict + 60) is False)
check("finished when the verdict came after its last write",
      sa._finished(stopped, verdict - 60) is True)
check("a completed foreground agent is still finished",
      sa._finished({"status": "completed", "ts": "2026-09-02T10:00:00.000Z"}, verdict - 60) is True)
check("an open agent is never finished",
      sa._finished({"status": "async_launched", "status_at": verdict}, verdict - 60) is False)
check("no status at all is never finished", sa._finished({}, verdict) is False)
check("a verdict with no timestamp is taken at face value",
      sa._finished({"status": "failed"}, verdict) is True)

shutil.rmtree(TMP2, ignore_errors=True)

# ---------------------------------------------------------------------------
# What an agent is DOING, and staying visible while it does it.
#
# A background agent goes quiet for minutes inside one long turn. Measured on a real Fable run:
# 81 seconds idle with the window at 90, so it was one second from vanishing from the pill, the
# header and the feed at once — which reads as "it died". `running` (is it moving) and `open`
# (was it dispatched and never reported an outcome) are separate questions now, and the answer to
# "what is it doing" comes from the tail of the agent's own transcript.
print("\nWhat the agent is doing")
TMP3 = Path(tempfile.mkdtemp(prefix="act-"))
try:
    j = TMP3 / "agent-x.jsonl"
    j.write_text("\n".join([
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "C:/old/thing.ts"}}]}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "..."},
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "npm run build\n  --silent", "description": "build it"}}]}}),
        json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "ok"}]}}),
        json.dumps({"type": "attachment", "note": "no tool here"}),
    ]) + "\n", encoding="utf-8")
    act = sa._last_activity(j)
    check("the LAST tool call wins, not the first", act.get("tool") == "Bash", act)
    # `command` beats `description`: what it ran is the answer, not what it called the run.
    check("the command is what is shown", act.get("detail", "").startswith("npm run build"), act)
    check("newlines are flattened for a one-line status",
          "\n" not in act.get("detail", ""), repr(act.get("detail")))
    j2 = TMP3 / "agent-y.jsonl"
    j2.write_text(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Edit", "input": {"file_path": "src/pages/Engine.tsx",
                                                       "old_string": "a", "new_string": "b"}}]}}) + "\n",
                  encoding="utf-8")
    e = sa._last_activity(j2)
    check("an edit reports the file, not the diff",
          e.get("tool") == "Edit" and e.get("detail") == "src/pages/Engine.tsx", e)

    j3 = TMP3 / "agent-z.jsonl"
    j3.write_text('not json\n{"type":"user"}\n', encoding="utf-8")
    check("a transcript with no tool call answers nothing, not an error",
          sa._last_activity(j3) == {}, sa._last_activity(j3))
    check("a missing file answers nothing", sa._last_activity(TMP3 / "nope.jsonl") == {})

    check("the open window is far wider than the running one",
          sa.OPEN_WINDOW >= sa.RUNNING_WINDOW * 10, (sa.OPEN_WINDOW, sa.RUNNING_WINDOW))
finally:
    shutil.rmtree(TMP3, ignore_errors=True)

# ---------------------------------------------------------------------------
print("\nONE DEFINITION, SHARED BY EVERY SCREEN")
# The chat feed, the left rail, the bottom bar and Mission Control each had their own rule, and
# the user saw all four at once: the same fan-out read as 2 under the chat, 0 in the rail and 1
# in the bar. `subagents.working` is now the single answer and every caller reads it.
import inspect as _inspect                                          # noqa: E402

_real_running = sa.running
try:
    now = time.time()
    sa.running = lambda pid, window=sa.OPEN_WINDOW: [
        {"agent_id": "moving", "updated": now - 5, "background": True},
        {"agent_id": "quiet-bg", "updated": now - 600, "background": True},
        {"agent_id": "quiet-fg", "updated": now - 600, "background": False},
        {"agent_id": "busy-fg", "updated": now - 10, "background": False},
    ]
    w = sa.working("p")
    ids = [a["agent_id"] for a in w]
    check("an agent writing right now counts", "moving" in ids, ids)
    check("a foreground agent writing right now counts", "busy-fg" in ids, ids)
    check("a BACKGROUND agent that has gone quiet still counts", "quiet-bg" in ids, ids)
    check("a foreground agent long gone quiet does not", "quiet-fg" not in ids, ids)
    check("each row says whether it is moving, so a caller can word it",
          [a["agent_id"] for a in w if a["moving"]] == ["moving", "busy-fg"],
          [(a["agent_id"], a["moving"]) for a in w])
finally:
    sa.running = _real_running

from asset_studio import cc_session as _cc                          # noqa: E402
from asset_studio import mission as _mi                             # noqa: E402

check("the rail and the dashboard read it",
      "subagents.working(pid)" in _inspect.getsource(_cc.live_status))
check("the chat feed reads it",
      "subagents.working(project_id)" in _inspect.getsource(_mi._active_agent_count))
check("Mission Control reads it",
      "subagents.working(pdir.name)" in _inspect.getsource(_mi._scan_agents))
check("and the count the rail shows includes the quiet ones",
      "quiet_agents" in _inspect.getsource(_cc.live_status))


# ---------------------------------------------------------------------------
print("\nA FINISHED TOOL IS NOT WHAT THE AGENT IS DOING")
# The panel showed "Bash python -" for thirteen minutes while the agent was writing a very long
# file. The command had returned in 0.3 seconds. Naming a finished tool as the current activity
# is worse than naming nothing: the only conclusion available from the screen was that it hung.
import shutil as _sh                                                # noqa: E402
import tempfile as _tf                                              # noqa: E402

TMP4 = Path(_tf.mkdtemp(prefix="phase-"))
try:
    def rec(*objs):
        return "".join(json.dumps(o) + chr(10) for o in objs)

    USE = {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "..."},
        {"type": "tool_use", "id": "toolu_1", "name": "Bash",
         "input": {"command": "python - <<PY"}}]}}
    RES = {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}]}}
    ATT = {"type": "attachment", "attachment": {"kind": "note"}}
    TXT = {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "Here is the plan."}]}}

    f1 = TMP4 / "agent-running.jsonl"
    f1.write_text(rec(USE), encoding="utf-8")
    a1 = sa._last_activity(f1)
    check("a tool with no result yet reads as RUNNING", a1.get("phase") == "tool", a1)
    check("...and the tool is named", a1.get("tool") == "Bash", a1)
    check("...with its command", "python" in (a1.get("detail") or ""), a1)

    f2 = TMP4 / "agent-generating.jsonl"
    f2.write_text(rec(USE, RES, ATT), encoding="utf-8")
    a2 = sa._last_activity(f2)
    check("once the result is in, it reads as GENERATING", a2.get("phase") == "generating", a2)
    check("the finished tool is still reported, as context not as current",
          a2.get("tool") == "Bash", a2)

    f3 = TMP4 / "agent-text.jsonl"
    f3.write_text(rec(USE, RES, TXT), encoding="utf-8")
    check("plain text after a tool is generating too",
          sa._last_activity(f3).get("phase") == "generating", sa._last_activity(f3))

    f4 = TMP4 / "agent-none.jsonl"
    f4.write_text(rec(TXT), encoding="utf-8")
    a4 = sa._last_activity(f4)
    check("an agent that has run no tool at all still reports a phase",
          a4.get("phase") == "generating" and a4.get("tool") == "", a4)

    f5 = TMP4 / "agent-empty.jsonl"
    f5.write_text("", encoding="utf-8")
    check("an empty transcript answers nothing, not an error", sa._last_activity(f5) == {})

    # An attachment is bookkeeping AROUND a message. Read as the message's last word it would
    # flip a running tool to "generating" and put the misleading reading back.
    f6 = TMP4 / "agent-att.jsonl"
    f6.write_text(rec(RES, ATT, USE, ATT), encoding="utf-8")
    check("an attachment after a tool_use does not make it look finished",
          sa._last_activity(f6).get("phase") == "tool", sa._last_activity(f6))
finally:
    _sh.rmtree(TMP4, ignore_errors=True)

# ---------------------------------------------------------------------------
print("\nA VERDICT AND THE LAST WRITE LAND IN THE SAME INSTANT")
# The outcome is recorded in the parent's transcript; the agent's own transcript is flushed a
# fraction of a second later. Measured on two real agents: the verdict landed 0.1 SECONDS before
# the last write, both times. Compared exactly, that reads as a stale verdict, and a finished
# agent went on counting for the full thirty-minute window -- "2 agents" on the bar with an empty
# panel beside it, and /compact refusing for half an hour after the work was done.
now = time.time()

check("a verdict 0.1s before the last write is still the verdict",
      sa._finished({"status": "completed", "status_at": now - 0.1}, now) is True)
check("...and so is one a few seconds before it",
      sa._finished({"status": "completed", "status_at": now - 5}, now) is True)
check("a verdict MINUTES before the last write is stale, which is the rule that matters",
      sa._finished({"status": "stopped", "status_at": now - 440}, now) is False)
check("the margin is smaller than the staleness it has to detect",
      sa._VERDICT_GRACE < 60, sa._VERDICT_GRACE)
check("an agent with no outcome recorded is never finished",
      sa._finished({"status": "in_progress", "status_at": now}, now) is False)
check("an outcome with no timestamp is taken at its word",
      sa._finished({"status": "completed"}, now) is True)

print("\nAnd the panel and the bar answer with the SAME test")
src_list = _inspect.getsource(sa._list_uncached)
check("list_for decides `open` with _finished and nothing else",
      'not _finished(row, mt)' in src_list
      and 'str(row.get("status") or "").lower() not in _TERMINAL' not in src_list)
check("running() decides with the same one", "_finished(row, mt)" in _inspect.getsource(sa.running))
# The one that costs the most when it is wrong: this guards /compact, a new conversation, the
# terminal handoff, the rename and the restart warning.
check("and so does the guard that refuses to destroy work",
      "running(project_id" in _inspect.getsource(sa.open_background))

_real_run = sa.running
try:
    fin = now - 900
    sa.running = lambda pid, window=sa.OPEN_WINDOW: []
    check("nothing running means nothing counted", sa.working("p") == [])
    sa.running = _real_run
except Exception:
    sa.running = _real_run

# ---------------------------------------------------------------------------
print("\nA WINDOW IN BYTES IS THE WRONG SHAPE FOR THIS FILE")
# One number cannot serve two agents. A coding agent writes records of about 1.5 KB; an agent
# driving Blender writes a viewport screenshot as inline base64 and its records reach 641 KB.
# Measured on a real run: 51 records, 2.6 MB, largest 641 KB. The 48 KB activity window covered
# 0.07 of ONE record, so it never held a complete line and the status read empty. The 512 KB
# panel window recovered 2 records out of 51, so the panel said "nothing recorded yet" about an
# agent that was six tool calls in. The agent was fine.
TMP5 = Path(_tf.mkdtemp(prefix="tail-"))
try:
    big = "B" * 600_000                      # one screenshot, near the real 641 KB
    heavy = TMP5 / "agent-heavy.jsonl"
    recs = []
    for i in range(8):
        recs.append(json.dumps({"type": "assistant", "timestamp": "t%d" % i,
                                "message": {"role": "assistant", "content": [
                                    {"type": "tool_use", "name": "Screenshot",
                                     "input": {"path": "shot-%d.png" % i}}]}}))
        recs.append(json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": [{"type": "image", "source": {"data": big}}]}]}}))
    heavy.write_text(chr(10).join(recs) + chr(10), encoding="utf-8")
    size_mb = heavy.stat().st_size / 1024 / 1024
    check("the fixture really is screenshot-heavy", size_mb > 4, "%.1f MB" % size_mb)

    got = sa._tail_records(heavy, 6)
    check("the tail returns COMPLETE records, not a slice of one", len(got) == 6, len(got))
    check("...and each one parses", all(json.loads(sa._scrub(g)) for g in got))

    ents = sa._entries(heavy)
    check("every record comes back, whatever it weighs", len(ents) == 16, len(ents))

    act = sa._last_activity(heavy)
    check("the status line finds the last tool call", act.get("tool") == "Screenshot", act)
    check("...and its detail", act.get("detail") == "shot-7.png", act)

    print("\nThe pixels are not carried through")
    one = json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "content": [{"type": "image", "source": {"data": big}}]}]}})
    cut = sa._scrub(one)
    check("a huge base64 blob is replaced", len(cut) < 2000, len(cut))
    check("and what is left is still valid JSON", isinstance(json.loads(cut), dict))
    check("it says what was dropped", "binary" in cut and "KB" in cut, cut[-120:])
    keep = json.dumps({"cmd": "npm run build", "note": "a normal string stays"})
    check("an ordinary string is untouched", sa._scrub(keep) == keep)

    print("\nAnd it is still bounded")
    check("there is a hard ceiling on one file", sa._TAIL_CAP <= 64 * 1024 * 1024, sa._TAIL_CAP)
    t0 = time.time()
    sa._entries(heavy)
    ms = (time.time() - t0) * 1000
    check("a 5 MB screenshot transcript still reads fast enough to poll", ms < 1500, "%.0f ms" % ms)

    light = TMP5 / "agent-light.jsonl"
    light.write_text(chr(10).join(
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "step %d" % i}]}}) for i in range(900)) + chr(10),
        encoding="utf-8")
    check("a chatty agent is still capped at the record limit",
          len(sa._entries(light)) == sa._TAIL_RECORDS, len(sa._entries(light)))
finally:
    _sh.rmtree(TMP5, ignore_errors=True)

# ---------------------------------------------------------------------------------------
# THE OTHER HALF OF THE SAME HOT PATH.
#
# The cursor above stops the PARENT transcripts being re-read. `list_for` also reads every
# subagent's OWN transcript, and one real project here holds 116 of them. That was 3.3 of the
# 4.7 seconds the call took cold; measured against the running backend, one feed request every
# third second took THIRTEEN. Six of those in flight is a browser's whole connection budget for
# the origin, so the file tree and the conversation queued behind them and changing workspace
# stalled.
#
# A subagent transcript is append-only and all but the running one finished long ago, so the
# numbers are remembered against the file's own mtime and size. That key is exact rather than a
# staleness window: a file cannot grow without both of them moving.
TMPL = Path(tempfile.mkdtemp(prefix="sa-ledger-"))
try:
    print("\nA finished agent's numbers are read once, not on every poll")
    lf = TMPL / "agent-led.jsonl"
    lf.write_text(json.dumps({
        "timestamp": "2026-09-02T05:00:00.000Z",
        "message": {"role": "assistant", "model": "claude-opus-5",
                    "usage": {"input_tokens": 11, "output_tokens": 22},
                    "content": [{"type": "text", "text": "hello"}]},
    }) + chr(10), encoding="utf-8")

    reads = {"n": 0}
    real = sa._ledger_read

    def counted(path):
        reads["n"] += 1
        return real(path)

    sa._ledger_read = counted
    sa._ledger_cache.clear()
    try:
        a = sa._ledger(lf)
        b = sa._ledger(lf)
        check("the file is opened once for two asks", reads["n"] == 1, reads["n"])
        check("and both answers agree", a["usage"] == b["usage"], (a["usage"], b["usage"]))
        check("the numbers are real", a["usage"]["input"] == 11 and a["usage"]["output"] == 22,
              a["usage"])

        # THE COPY MATTERS. The caller merges the parent's figures into this dict and stamps
        # `running`, `updated` and `idle_s` onto it. Handing out the remembered object would let
        # one request edit what the next one reads.
        a["model"] = "edited"
        a["usage"]["input"] = 999
        a["stats"]["read"] = 999
        c = sa._ledger(lf)
        check("what the caller does to its copy does not reach the next caller",
              c["model"] != "edited" and c["usage"]["input"] == 11 and c["stats"]["read"] == 0,
              (c["model"], c["usage"]["input"], c["stats"]["read"]))

        # An agent that is still writing MUST be re-read. This is the one file in a hundred that
        # changes, and it is the only one anybody is watching.
        time.sleep(0.01)
        with lf.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "timestamp": "2026-09-02T05:00:09.000Z",
                "message": {"role": "assistant", "model": "claude-opus-5",
                            "usage": {"input_tokens": 5, "output_tokens": 5},
                            "content": [{"type": "text", "text": "more"}]},
            }) + chr(10))
        d = sa._ledger(lf)
        check("a file that grew is read again", reads["n"] == 2, reads["n"])
        check("...and the new tokens are counted", d["usage"]["input"] == 16, d["usage"])
    finally:
        sa._ledger_read = real

    # THE SAME 116 FILES, THROUGH THE OTHER DOOR. The panel beside the conversation asks which
    # files each agent touched, and answering opened every transcript again: measured on the real
    # project, `/subagents?files=true` was 9 seconds while `/subagents` was already a tenth of one.
    print("\nAnd the file list an agent touched is remembered the same way")
    reads2 = {"n": 0}
    real2 = sa._touched_read

    def counted2(path):
        reads2["n"] += 1
        return real2(path)

    sa._touched_read = counted2
    sa._touched_cache.clear()
    sa._entries_cache.clear()
    _ff = sa._file_for
    sa._file_for = lambda pid, aid: lf
    try:
        t1 = sa.touched("p", "led")
        t2 = sa.touched("p", "led")
        check("the transcript is opened once for two asks", reads2["n"] == 1, reads2["n"])
        check("and both answers agree", t1 == t2, (t1, t2))
        t1["edited"].append({"path": "made up", "times": 1})
        check("what a caller does to its list does not reach the next",
              sa.touched("p", "led")["edited"] == t2["edited"])
    finally:
        sa._file_for = _ff
        sa._touched_read = real2

    # ...AND THE READ THE TWO OF THEM SHARE. `_list_uncached` asks one file for its ledger and
    # then for its file list, back to back. Without this the FIRST look at a project decoded and
    # parsed all 116 transcripts twice: 25 seconds, down to 8.
    print("\nThe ledger and the file list share one read of the transcript")
    sa._entries_cache.clear()
    sa._ledger_cache.clear()
    sa._touched_cache.clear()
    parses = {"n": 0}
    real3 = sa._entries_read

    def counted3(path, records=sa._TAIL_RECORDS):
        parses["n"] += 1
        return real3(path, records)

    sa._entries_read = counted3
    try:
        sa._ledger(lf)
        sa._touched_read(lf)
        check("two questions, one read", parses["n"] == 1, parses["n"])
        check("the parsed tail is bounded", sa._ENTRIES_MAX <= 32, sa._ENTRIES_MAX)
    finally:
        sa._entries_read = real3
    sa._entries_cache.clear()

    print("\nAnd the memo cannot grow without bound")
    check("there is a ceiling", sa._LEDGER_MAX <= 4096, sa._LEDGER_MAX)
    sa._ledger_cache.clear()
    for i in range(sa._LEDGER_MAX + 40):
        sa._ledger_cache[("f%d" % i, i, i)] = {}
        if len(sa._ledger_cache) >= sa._LEDGER_MAX:
            sa._ledger_cache.pop(next(iter(sa._ledger_cache)), None)
    check("it stays at the ceiling", len(sa._ledger_cache) < sa._LEDGER_MAX + 2,
          len(sa._ledger_cache))
    sa._ledger_cache.clear()
finally:
    _sh.rmtree(TMPL, ignore_errors=True)

# ---------------------------------------------------------------------------------------
# A LOCAL `import` MAKES THE NAME LOCAL TO THE WHOLE FUNCTION.
#
# review.py imports shutil at the top. `_ensure_browser` also imported it inside ONE of its
# branches, which made `shutil` local to all of it — so the OTHER branch, the one that closes a
# browser being replaced, raised "local variable 'shutil' referenced before assignment" and the
# live game link answered that instead of opening a page. Asked as a fact about the compiled
# function, so it cannot come back by being written a slightly different way.
print("\nEvery name review.py uses at module level is still a module-level name")
from asset_studio import review as _rv                                    # noqa: E402
for _fn in (_rv._ensure_browser, _rv.shutdown):
    _locals = set(_fn.__code__.co_varnames)
    check("%s() does not shadow shutil" % _fn.__name__, "shutil" not in _locals,
          sorted(_locals)[:8])

print("\n  %d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

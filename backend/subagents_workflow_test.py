"""Are Workflow-tool agents counted, named and closed like every other agent?

A Workflow run writes its agents ONE FOLDER DOWN from the Task agents:

    <session>/subagents/workflows/wf_<run>/agent-<id>.jsonl     (+ agent-<id>.meta.json)
    <session>/subagents/workflows/wf_<run>/journal.jsonl        "started" / "result" per agent
    <session>/workflows/wf_<run>.json                           written when the run ends

Every count in `subagents` scanned the first folder only, so four Opus 5.5 builders worked for
twenty minutes while the rail, the bottom bar and the needs-you strip all said 0. The Workflows
tab saw them, but named the run "workflow" (its script sat under another project folder of the
same session) and called a quiet agent "done". This file holds all of that in place, on a fake
projects folder, so nothing here reads the real transcripts.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from asset_studio import mission, subagents as sa, workflows as wf   # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, extra))


def line(o):
    return json.dumps(o) + "\n"


def transcript(path: Path, model="claude-opus-5-5", prompt="[Workflow harness] do the thing"):
    path.write_text(
        line({"type": "user", "isSidechain": True, "timestamp": "2026-09-23T03:17:00.000Z",
              "message": {"role": "user", "content": prompt}})
        + line({"type": "assistant", "isSidechain": True, "timestamp": "2026-09-23T03:18:00.000Z",
                "message": {"role": "assistant", "model": model,
                            "usage": {"input_tokens": 10, "output_tokens": 5},
                            "content": [{"type": "tool_use", "name": "Edit", "id": "t1",
                                         "input": {"file_path": "C:/x/live_scene.py",
                                                   "old_string": "a", "new_string": "b"}}]}}),
        encoding="utf-8")


def age(path: Path, seconds: float):
    t = time.time() - seconds
    os.utime(path, (t, t))


ROOT = Path(tempfile.mkdtemp(prefix="sa-wf-"))
PID = "c--fake-STUDIO"
SID = "11111111-2222-3333-4444-555555555555"
real_dir = mission.claude_projects_dir
mission.claude_projects_dir = lambda: ROOT
try:
    pdir = ROOT / PID
    sess = pdir / SID
    sub = sess / "subagents"
    run = sub / "workflows" / "wf_run1"
    run.mkdir(parents=True)
    (pdir / (SID + ".jsonl")).write_text("", encoding="utf-8")   # the parent: nothing about them

    # Agent A works; agent B has returned; a Task agent C sits beside them the old way.
    for aid, desc in (("aaa", "A: scene API"), ("bbb", "B: runtime")):
        transcript(run / ("agent-%s.jsonl" % aid))
        (run / ("agent-%s.meta.json" % aid)).write_text(json.dumps(
            {"agentType": "workflow-subagent", "description": desc, "workflowPhase": "Build"}),
            encoding="utf-8")
    (run / "journal.jsonl").write_text(
        line({"type": "launched"})
        + line({"type": "started", "key": "v2:1", "agentId": "aaa", "label": "A: scene API",
                "phase": "Build"})
        + line({"type": "started", "key": "v2:2", "agentId": "bbb", "label": "B: runtime",
                "phase": "Build"})
        + line({"type": "result", "key": "v2:2", "agentId": "bbb", "result": {"ok": True}})
        + '{"type":"started","agentId":"half-writ',                 # a line still being written
        encoding="utf-8")
    transcript(sub / "agent-ccc.jsonl", model="claude-opus-5")

    print("A Workflow agent that is still working is counted")
    rows = {r["agent_id"]: r for r in sa.running(PID)}
    check("the working Workflow agent is there", "aaa" in rows, sorted(rows))
    check("the one whose result is in the journal is not", "bbb" not in rows, sorted(rows))
    check("the Task agent beside them still counts", "ccc" in rows, sorted(rows))
    a = rows.get("aaa") or {}
    check("it is named from the journal, not from its prompt", a.get("description") == "A: scene API",
          a.get("description"))
    check("its run and phase ride along", (a.get("workflow"), a.get("workflow_phase")) == ("wf_run1", "Build"),
          (a.get("workflow"), a.get("workflow_phase")))
    check("its model is read from its own records", a.get("model") == "claude-opus-5-5", a.get("model"))
    check("a half-written journal line is skipped, not fatal", "half-writ" not in sa.wf_journal(run)["done"])

    print("\nA quiet Workflow agent is owed a result, so it goes on counting")
    age(run / "agent-aaa.jsonl", 300)          # a long think: nothing written for five minutes
    age(sub / "agent-ccc.jsonl", 300)
    w = {r["agent_id"]: r for r in sa.working(PID)}
    check("the quiet Workflow agent still counts", "aaa" in w, sorted(w))
    check("...and says it is quiet, not moving", w.get("aaa", {}).get("moving") is False,
          w.get("aaa", {}).get("moving"))
    check("a quiet FOREGROUND Task agent does not (unchanged)", "ccc" not in w, sorted(w))
    check("it guards a respawn like a background Task agent",
          "aaa" in {r["agent_id"] for r in sa.open_background(PID)})
    check("the shared count says 1", mission._active_agent_count(pdir, PID) == 1,
          mission._active_agent_count(pdir, PID))

    print("\nThe list, the panel and the file lookup find it")
    sa._list_cache.clear()
    L = sa.list_for(PID, with_files=True)
    lr = {r["agent_id"]: r for r in L["agents"]}
    check("the list has both agents of the live run", {"aaa", "bbb"} <= set(lr), sorted(lr))
    check("the working one is open, not finished", lr.get("aaa", {}).get("open") is True, lr.get("aaa"))
    check("the returned one is marked completed", lr.get("bbb", {}).get("status") == "completed",
          lr.get("bbb", {}).get("status"))
    check("...and is neither running nor open",
          not lr.get("bbb", {}).get("running") and not lr.get("bbb", {}).get("open"))
    check("each row says which session it is from", lr.get("aaa", {}).get("session") == SID,
          lr.get("aaa", {}).get("session"))
    check("its edits are read from its own tool calls",
          [e["path"] for e in lr.get("aaa", {}).get("touched", {}).get("edited", [])] == ["C:/x/live_scene.py"],
          lr.get("aaa", {}).get("touched"))
    # Resolved on both sides: the temp folder is handed out in its 8.3 short form (ADMINI~1),
    # and the lookup resolves the project folder to the long one.
    found = sa._file_for(PID, "aaa")
    check("the file lookup finds a Workflow agent",
          found is not None and found.resolve() == (run / "agent-aaa.jsonl").resolve(), found)
    d = sa.detail(PID, "aaa", limit=10)
    check("its panel opens", d.get("ok") is True, d.get("error"))
    check("...under its real session, not the folder named workflows",
          (d.get("agent") or {}).get("session") == SID, (d.get("agent") or {}).get("session"))
    check("...with its name", (d.get("agent") or {}).get("description") == "A: scene API")

    print("\nThe Workflows tab: the right name, the right state, the right phase")
    other = ROOT / (PID + "-backend") / SID / "workflows" / "scripts"
    other.mkdir(parents=True)
    (other / "studio-agent-tools-wf_run1.js").write_text("export const meta = {}", encoding="utf-8")
    check("the run is named from a script under ANOTHER project folder of the same session",
          wf._scripts_name(pdir, "wf_run1", SID) == "studio-agent-tools", wf._scripts_name(pdir, "wf_run1", SID))
    check("without the session it falls back as before", wf._scripts_name(pdir, "wf_run1") == "workflow")
    agents = {x["agentId"]: x for x in wf._agents_from_dir(run, time.time())}
    check("a quiet agent with no result is running, not done", agents["aaa"]["state"] == "running",
          agents["aaa"]["state"])
    check("an agent with a result is done", agents["bbb"]["state"] == "done", agents["bbb"]["state"])
    check("cards carry their names", agents["aaa"]["label"] == "A: scene API", agents["aaa"]["label"])
    check("...and their phase", (agents["aaa"]["phaseTitle"], agents["aaa"]["phaseIndex"]) == ("Build", 1),
          (agents["aaa"]["phaseTitle"], agents["aaa"]["phaseIndex"]))
    live, running, _newest = wf._dir_liveness(run, time.time())
    check("the run is live with one agent running", (live, running) == (True, 1), (live, running))
    s = wf._summarize_dir(PID, pdir, "wf_run1", run)
    check("the run summary is named", s["name"] == "studio-agent-tools", s["name"])
    loose = wf._agents_from_dir(sub, time.time())
    check("a loose Task batch keeps the old rule", all(x["label"] for x in loose) and len(loose) == 1,
          [(x["agentId"], x["state"]) for x in loose])

    print("\nMission Control names it from its meta file")
    ags = mission._scan_agents([pdir], {})
    labels = {x.agent_id: x.label for x in ags}
    check("the Control tab label is the given name", labels.get("aaa") == "A: scene API", labels)

    print("\nWhen the run ends, every agent of it is finished")
    (sess / "workflows").mkdir()
    (sess / "workflows" / "wf_run1.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    check("nothing of the ended run is running", not any(r.get("workflow") for r in sa.running(PID, window=sa.OPEN_WINDOW)),
          sa.running(PID, window=sa.OPEN_WINDOW))
    sa._list_cache.clear()
    lr = {r["agent_id"]: r for r in sa.list_for(PID)["agents"]}
    check("the agent that never returned is marked stopped", lr.get("aaa", {}).get("status") == "stopped",
          lr.get("aaa", {}).get("status"))
    check("...and is not open", lr.get("aaa", {}).get("open") is False, lr.get("aaa", {}).get("open"))

    print("\nAn ended run leaves the list after half an hour; the Workflows tab keeps it")
    age(sess / "workflows" / "wf_run1.json", sa.OPEN_WINDOW + 60)
    sa._list_cache.clear()
    lr = {r["agent_id"]: r for r in sa.list_for(PID)["agents"]}
    check("the old run's agents are not read at all", not ({"aaa", "bbb"} & set(lr)), sorted(lr))
    check("the Task agent is still listed", "ccc" in lr, sorted(lr))

    print("\nA project with no Workflow runs costs what it did")
    shutil.rmtree(sub / "workflows")
    check("no runs, no rows", [r for r in sa.running(PID, window=sa.OPEN_WINDOW) if r.get("workflow")] == [])
    check("the runs lookup on a folder with none is empty", sa._wf_runs(sub) == [])
finally:
    mission.claude_projects_dir = real_dir
    shutil.rmtree(ROOT, ignore_errors=True)

print("\n  %d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

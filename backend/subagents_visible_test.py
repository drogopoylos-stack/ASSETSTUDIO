"""Is a helper listed for as long as it works, however it was started?

Measured on 2026-11-04 with a foreground helper in one 130-second command: the row had no name, and
it left the list of the agents button at 92 seconds, while the helper worked for 136 seconds. Only
a background helper was "owed" a result, so only /orch-style fan-outs stayed on screen.
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

from asset_studio import mission                # noqa: E402
from asset_studio import subagents as sa        # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, extra))


TMP = Path(tempfile.mkdtemp(prefix="sa-visible-"))
PDIR = TMP / "projects" / "d--Test"
SESS = "s1"
SUB = PDIR / SESS / "subagents"
SUB.mkdir(parents=True)
PARENT = PDIR / (SESS + ".jsonl")
PARENT.write_text("", encoding="utf-8")
mission_project_dir = mission.project_dir
mission.project_dir = lambda pid: (TMP / "projects", PDIR, None)


def helper(aid, quiet_s, shape="foreground", finished=False, meta=True):
    f = SUB / ("agent-%s.jsonl" % aid)
    f.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "go"}}) + "\n",
                 encoding="utf-8")
    side = f.with_name(f.stem + ".meta.json")
    if meta:
        body = {"agentType": "general-purpose", "description": "Helper " + aid, "model": "haiku",
                "toolUseId": "toolu_" + aid, "requestShape": shape}
        if finished:
            body["finished"] = True
        side.write_text(json.dumps(body), encoding="utf-8")
    when = time.time() - quiet_s
    os.utime(f, (when, when))
    if meta:
        os.utime(side, (when, when))
    return f


def answer(aid, with_record=False):
    line = {"type": "user", "timestamp": "2026-11-04T10:00:00.000Z", "message": {"role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_" + aid, "content": "Interrupted"}]}}
    if with_record:
        line["toolUseResult"] = {"agentId": aid, "status": "completed", "agentType": "general-purpose"}
    with open(PARENT, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, separators=(",", ":")) + "\n")


def fresh():
    sa._list_cache.clear()
    return {a["agent_id"]: a for a in sa._list_uncached("d--Test")["agents"]}


try:
    print("A foreground helper in one long quiet command")
    helper("quiet", 300)
    row = fresh()["quiet"]
    check("it is owed a result", row["open"] is True and row["running"] is False, row)
    check("it has its name from the first second", row.get("description") == "Helper quiet", row)
    check("it has its type", row.get("agent_type") == "general-purpose", row)
    check("it is not called a background agent", not row.get("background"), row)
    check("the status poll counts it", "quiet" in {a["agent_id"] for a in sa.working("d--Test")})
    check("and calls it quiet", not [a for a in sa.working("d--Test") if a["agent_id"] == "quiet"][0]["moving"])
    check("the guard of /compact does not wait for it", not sa.open_background("d--Test"))

    print("A foreground helper that writes")
    helper("busy", 5)
    row = fresh()["busy"]
    check("it is moving", row["running"] is True, row)
    check("it has its name", row.get("description") == "Helper busy", row)

    print("An outcome ends it")
    answer("quiet")
    row = fresh()["quiet"]
    check("an interrupted helper leaves the list", row["open"] is False and row["running"] is False, row)
    check("it reads as stopped", row.get("status") == "stopped", row)
    check("the status poll agrees", "quiet" not in {a["agent_id"] for a in sa.working("d--Test")})

    helper("flag", 300, finished=True)
    row = fresh()["flag"]
    check("the finished flag ends it", row["open"] is False and row.get("status") == "completed", row)

    helper("late", 120)
    side = SUB / "agent-late.meta.json"
    row = fresh()["late"]
    check("no flag yet, so it is owed", row["open"] is True, row)
    side.write_text(json.dumps({"description": "Helper late", "finished": True}), encoding="utf-8")
    now = time.time()
    os.utime(SUB / "agent-late.jsonl", (now, now))
    row = fresh()["late"]
    check("the flag written at the end is seen at once", row.get("status") == "completed", row)
    check("and the agent has stopped", row["running"] is False, row)

    print("A helper resumed after its outcome")
    helper("again", 2, finished=True)
    side = SUB / "agent-again.meta.json"
    old = time.time() - 300
    os.utime(side, (old, old))
    row = fresh()["again"]
    check("its own writes beat the old verdict", row["running"] is True, row)

    print("A background helper keeps its old meaning")
    helper("bg", 600, shape="background")
    row = fresh()["bg"]
    check("it is owed", row["open"] is True and row.get("background") is True, row)
    check("the guard of /compact waits for it", "bg" in {a["agent_id"] for a in sa.open_background("d--Test")})

    print("An old agent with no sidecar")
    helper("old", 200, meta=False)
    check("it is listed and owed", fresh()["old"]["open"] is True)
    helper("gone", sa.OPEN_WINDOW + 60, meta=False)
    check("it leaves the list after the long window", fresh()["gone"]["open"] is False)
    helper("done", 200, meta=False)
    answer("done", with_record=True)
    sa._scan_cursor.clear()
    check("a parent record still ends it", fresh()["done"]["open"] is False)

    print("The sidecar cannot damage the row")
    f = helper("bad", 5)
    f.with_name(f.stem + ".meta.json").write_text("{not json", encoding="utf-8")
    check("a broken sidecar is ignored", fresh()["bad"]["running"] is True)
    f.with_name(f.stem + ".meta.json").write_text("[1]", encoding="utf-8")
    check("a sidecar with another shape is ignored", fresh()["bad"]["running"] is True)
finally:
    mission.project_dir = mission_project_dir
    shutil.rmtree(TMP, ignore_errors=True)

print("\n  %d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

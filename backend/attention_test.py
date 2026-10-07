# -*- coding: utf-8 -*-
"""Who needs you: the three states, the order, and the switch.

The bar at the top of the workspace is the first thing a person reads, so a wrong state there is
worse than no bar. Three claims are checked:

  * BLOCKED beats WORKING. A project whose last turn ended on a question is blocked even while
    its transcript is still being written — `awaiting_input` wins over `working`, because a
    session that is asking is not making progress.
  * A project with a running subagent of its own is WORKING even when its own transcript is
    quiet: that is exactly the case that used to read as "nothing happening".
  * The quiet ones are a COUNT, never a list. Fifty-two names is not one line.

And the order: blocked first, then the one that has been waiting longest — the order a person
would work through them, rather than alphabetical, which puts the same project on top every day.

Run:  backend/.venv/Scripts/python.exe backend/attention_test.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from asset_studio import mission                     # noqa: E402
from asset_studio.config import settings             # noqa: E402

passed = 0
fails = []


def ok(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
        print("  PASS  %s" % name)
        return
    fails.append(name + ("   <- " + str(extra) if extra else ""))
    print("  FAIL  %s   %s" % (name, extra))


NOW = time.time()


def project(name, **kw):
    row = {"id": name + "-id", "path": "C:/work/" + name, "name": name,
           "awaiting_input": False, "working": False, "agents_active": 0,
           "last_activity": NOW, "last_summary": "", "todo_in_progress": ""}
    row.update(kw)
    return row


FAKE = {"projects": [
    project("quiet-one"),
    project("quiet-two"),
    project("writing", working=True),
    project("asked-recently", awaiting_input=True, last_activity=NOW - 30,
            last_summary="  Which  of the two  should it be? "),
    project("asked-ages-ago", awaiting_input=True, last_activity=NOW - 4000),
    # A question AND a live transcript: still blocked. This is the case the rail gets wrong.
    project("asked-while-writing", awaiting_input=True, working=True),
    # Quiet itself, but a subagent of its own is running.
    project("delegating", agents_active=3),
    # A transcript folder whose project no longer exists on disk: no path, so not a row at all.
    project("ghost", path=""),
]}

real_overview = mission.overview
try:
    mission.overview = lambda *a, **k: FAKE            # type: ignore[assignment]
    mission._ATTN.drop()
    a = mission.attention()
finally:
    mission.overview = real_overview                   # type: ignore[assignment]
    mission._ATTN.drop()

by = {r["name"]: r for r in a["rows"]}

print("\nThe three states")
ok("a question is blocked", by.get("asked-recently", {}).get("state") == "blocked")
ok("...even while the transcript is being written",
   by.get("asked-while-writing", {}).get("state") == "blocked",
   by.get("asked-while-writing", {}).get("state"))
ok("a live transcript is working", by.get("writing", {}).get("state") == "working")
ok("a running subagent is working too", by.get("delegating", {}).get("state") == "working",
   by.get("delegating", {}).get("state"))
ok("the quiet ones are not listed", "quiet-one" not in by and "quiet-two" not in by,
   sorted(by))
ok("...they are counted", a["counts"]["idle"] == 2, a["counts"]["idle"])
ok("a project with no folder is left out entirely", "ghost" not in by)

print("\nThe counts")
ok("three are blocked", a["counts"]["blocked"] == 3, a["counts"]["blocked"])
ok("two are working", a["counts"]["working"] == 2, a["counts"]["working"])
ok("the subagents are totalled", a["counts"]["agents"] == 3, a["counts"]["agents"])

print("\nThe order you would work through them")
names = [r["name"] for r in a["rows"]]
ok("every blocked one comes before every working one",
   max(i for i, r in enumerate(a["rows"]) if r["state"] == "blocked")
   < min(i for i, r in enumerate(a["rows"]) if r["state"] == "working"), names)
ok("...longest wait first", names.index("asked-ages-ago") < names.index("asked-recently"), names)

print("\nWhat a row carries")
r = by["asked-recently"]
ok("the path, for the workspace to open", r["path"] == "C:/work/asked-recently", r["path"])
ok("the summary, on one line", r["why"] == "Which of the two should it be?", repr(r["why"]))
ok("how long it has been waiting", 25 < r["since"] < 120, r["since"])

print("\nThe switch")
# The CACHE is changed, not the file: a test must never rewrite the user's settings.json.
# `reload()` afterwards throws the change away and reads the real file again.
try:
    settings.all()["needs_you"] = False
    from asset_studio.routers import mission as mr
    off = mr.attention()
    ok("off means an empty answer, not an error", off.get("ok") is True and off.get("off") is True)
    ok("...with nothing in it", off["rows"] == [] and off["counts"]["blocked"] == 0)
finally:
    settings.reload()

print("\n%d passed, %d failed" % (passed, len(fails)))
for f_ in fails:
    print("  - " + f_)
sys.exit(1 if fails else 0)

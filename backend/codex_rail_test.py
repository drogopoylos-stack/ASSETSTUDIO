# -*- coding: utf-8 -*-
"""Does the subagents button on the left work for CODEX?

The button is two things that have to agree: a COUNT on the rail (which is why you click) and a LIST
in the panel (what you get). `codex_app.subagents` and the route in front of it are covered by
`codex_app_test.py` [4a]. What was NOT covered is the count, and the count is the half that decides
whether the button ever lights up:

  * `live_status` computes the rail's fan-out in TWO places — a Claude loop over `_live`, and a
    separate Codex loop over `codex_app.live_rows()`. `live_status_test.py` populates `_live` only,
    so every check it makes would still pass if the Codex loop were deleted outright.
  * A Codex conversation is not in `_live` at all. It runs in the Codex app-server, and the only
    thing that knows its subagents is `_CONVS`.
  * `live_rows` counts `subs[...]["running"]`, which is set from Codex's own turn/started and
    turn/completed notes. An agent inside a ten-minute tool call stays counted — the case that made
    the rail go dark for Claude, and that must not be reintroduced here.

So this test stands a Codex conversation up in `_CONVS` (no real Codex is spawned), asks the same
`live_status()` the rail polls, and then follows the answer down the path the PANEL takes:
`altFeedPrefix` -> `codex--<folder>` -> `mission.project_subagents` -> the `running || open` filter.

Run:  backend/.venv/Scripts/python.exe backend/codex_rail_test.py
"""
import io
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# The Codex module writes its thread logs and project indexes under DATA_DIR. Point them at a
# throwaway before anything is imported, so this test cannot touch the running Studio's own files.
_tmp = Path(tempfile.mkdtemp(prefix="codex-rail-test-"))

from asset_studio import cc_session as cc          # noqa: E402
from asset_studio import codex_app as cx           # noqa: E402
from asset_studio import deepseek_session as ds    # noqa: E402
from asset_studio import mission as mi             # noqa: E402

cx._THREADS = _tmp / "threads"
cx._PROJECTS = _tmp / "projects"

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, extra))


BARE = "d--codex-rail-project"
SUB = "01a1-rail-sub"

saved_convs = dict(cx._CONVS)
saved_live = dict(cc._live)
real_ds_rows = ds.live_rows
try:
    cx._CONVS.clear()
    cc._live.clear()
    ds.live_rows = lambda: []          # this machine's own harness session; not what is under test

    conv = cx._conv(BARE)
    conv.cwd = str(_tmp)
    conv.thread_id = "01a1-rail-parent"
    conv.working = True
    # A subagent that is running but has not written for five minutes: inside one long tool call.
    # Still dispatched, still owed a result — and still owed a count on the rail.
    conv.subs[SUB] = {"started": time.time() - 600, "running": True, "path": "/root/rail_probe",
                      "parent": conv.thread_id, "activity": {"tool": "Running", "detail": "pytest"},
                      "last": time.time() - 300}

    print("The rail's count, which is what makes the button light up")
    rows = cx.live_rows()
    check("live_rows reports the Codex conversation and its running agent",
          rows == [(BARE, True, 1)], rows)

    st = cc.live_status()
    check("the workspace reads as working", st["statuses"].get(BARE) is True, st["statuses"])
    # The rail filters `running_agents` by the ids the workspace LIST knows, which are bare. The
    # prefixed key is there too, so either lookup works.
    check("the fan-out is keyed by the BARE folder id the workspace list knows",
          st["running_agents"].get(BARE) == 1, st["running_agents"])
    check("...and by the prefixed id the feed uses",
          st["running_agents"].get(cx.CODEX_PREFIX + BARE) == 1, st["running_agents"])
    # `busyAgent[bare]` is what the Workspace turns into the feed prefix. If this says claude, the
    # panel asks Claude's transcript layout about a Codex agent.
    check("the engine reported for the folder is codex, not claude",
          st["agents"].get(BARE) == "codex", st["agents"])
    check("the total counts it once, not once per key",
          st["running_agents_total"] == 1, st["running_agents_total"])

    print("\nThe panel's list, through the route the UI actually calls")
    feed = cx.CODEX_PREFIX + BARE
    listed = mi.project_subagents(feed)
    mine = next((a for a in listed["agents"] if a["agent_id"] == SUB), None)
    check("the Codex agent is listed by the route the panel calls", mine is not None, listed)
    # RunningAgents keeps `a.running || a.open`. An agent quiet for five minutes must survive that
    # filter, or the rail says 2 and the panel beside it says nothing.
    check("...and survives the panel's running-or-open filter",
          bool(mine) and (mine.get("running") or mine.get("open")), mine)
    check("...and is named, so the card is not a blank row",
          bool(mine) and mine.get("description") == "rail_probe", mine)
    check("...and still shows what it is doing, not merely that it exists",
          bool(mine) and (mine.get("activity") or {}).get("tool") == "Running", (mine or {}).get("activity"))
    check("the panel's counts agree with the rail's",
          listed["running"] + listed["open"] == 1, listed)
    check("one agent opens through the same route",
          mi.project_subagent(feed, SUB)["agent"]["agent_id"] == SUB)

    print("\nThe prefix both sides build, which is how the pane finds the agent")
    # The frontend builds it with altFeedPrefix() from a hard-coded list; the backend builds it with
    # CODEX_PREFIX. If one is renamed, the rail counts an agent the pane then cannot open, and
    # nothing else in either test suite notices.
    ts = (Path(__file__).resolve().parent.parent / "frontend" / "src" / "pages" / "Workspace.tsx").read_text(
        encoding="utf-8", errors="replace")
    m = re.search(r"BUILTIN_ALT_AGENTS\s*=\s*\[([^\]]*)\]", ts)
    names = re.findall(r"\"([^\"]+)\"", m.group(1)) if m else []
    check("the frontend lists codex as an engine with its own feed prefix", "codex" in names, names)
    check("...and that prefix is the one the backend serves",
          cx.CODEX_PREFIX == "codex--", cx.CODEX_PREFIX)

    print("\nA fan-out that has gone quiet is not a fan-out that has stopped")
    # The failure `live_status` was changed to fix for Claude: the parent hands work out, goes
    # silent, and the count vanished with it. Codex must not answer that question differently.
    conv.subs[SUB]["last"] = time.time() - 3600
    st2 = cc.live_status()
    check("an agent silent for an hour is still counted",
          st2["running_agents"].get(BARE) == 1, st2["running_agents"])
    check("...but the card says quiet rather than writing",
          (mi.project_subagents(feed)["agents"][0].get("running") is False), None)
    check("...and the panel still shows it", mi.project_subagents(feed)["open"] == 1,
          mi.project_subagents(feed))

    print("\nNothing is claimed for a folder with no Codex conversation")
    st3 = cc.live_status()
    check("an unknown folder has no count and no engine",
          BARE + "-other" not in st3["running_agents"] and BARE + "-other" not in st3["agents"])
finally:
    ds.live_rows = real_ds_rows
    cx._CONVS.clear()
    cx._CONVS.update(saved_convs)
    cc._live.clear()
    cc._live.update(saved_live)

print("\n  %d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

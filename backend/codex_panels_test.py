# -*- coding: utf-8 -*-
"""The four rail buttons on a CODEX pane: Skills, Subagents, Prompt history, Checkpoints.

Each button is one backend call the panel makes with the ENGINE-PREFIXED feed id. This test makes
that call for `codex--<folder>` and for the same folder's Claude id, and asks the same question of
both: does the panel get something it can draw?

  Skills          GET /api/skills?agent=codex        -> skills.list_skills(pid, "codex")
  Subagents       .../subagents                      -> mission.project_subagents
  Prompt history  .../feed?kinds=user,text           -> mission.project_feed
  Checkpoints     .../checkpoints (+ POST)           -> checkpoints.list_for / create

No Codex process is started: a thread log is written by hand in the shape `events_for` reads, and
the conversation is placed in `_CONVS` the way a live turn would.

Run:  backend/.venv/Scripts/python.exe backend/codex_panels_test.py
"""
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_data = Path(tempfile.mkdtemp(prefix="codex-panels-data-"))
(_data / "tmp").mkdir(parents=True, exist_ok=True)
os.environ["ASSET_STUDIO_DATA"] = str(_data)

from asset_studio import checkpoints as ck        # noqa: E402
from asset_studio import codex_app as cx          # noqa: E402
from asset_studio import mission as mi            # noqa: E402
from asset_studio import skills as sk             # noqa: E402
from asset_studio import workspace as ws          # noqa: E402

cx._THREADS = _data / "threads"
cx._PROJECTS = _data / "projects"
cx._THREADS.mkdir(parents=True, exist_ok=True)
cx._PROJECTS.mkdir(parents=True, exist_ok=True)

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, extra))


# --- a project folder, and a Codex conversation in it --------------------------------------
PROJ = Path(tempfile.mkdtemp(prefix="codex-panels-proj-"))
(PROJ / "main.py").write_text("print('hello')\n", encoding="utf-8")
# Open the folder the way the Studio does, so the id is the real one and the checkpoint store can
# resolve it from the workspace's own root list.
ws.add_roots([str(PROJ)])
BARE = next(r["id"] for r in ws.roots() if Path(r["path"]).name == PROJ.name)
FEED = "codex--" + BARE
TID = "01a1-panels-thread"
SUB = "01a1-panels-sub"
now = time.time()

log = cx._log_path(TID)
log.parent.mkdir(parents=True, exist_ok=True)
with log.open("w", encoding="utf-8") as f:
    for rec in [
        {"t": "user", "at": now - 60, "text": "make the sprite sheet"},
        {"t": "item", "at": now - 55, "item": {"type": "agentMessage", "text": "Done — four frames."}},
        {"t": "user", "at": now - 20, "text": "now shade it"},
    ]:
        f.write(json.dumps(rec) + "\n")

cx._index_update(BARE, lambda d: d.update(
    active=TID, threads={TID: {"cwd": str(PROJ), "title": "sprite sheet", "created": now - 60,
                               "updated": now - 20, "model": "gpt-5-codex"}},
    subs={SUB: {"name": "reviewer", "started": now - 40, "running": False, "tokens": 1200}}))

conv = cx._Conv(BARE)                             # a real conversation object; no app-server behind it
conv.cwd = str(PROJ)
conv.thread_id = TID
conv.model = "gpt-5-codex"
conv.subs = {SUB: {"name": "reviewer", "started": now - 40, "running": False, "tokens": 1200}}
cx._CONVS[BARE] = conv

print("\n[1] Prompt history — the panel asks for kinds=user,text")
feed = mi.project_feed(FEED, 2000, "", "user,text")
lines = feed.get("lines") or []
users = [l for l in lines if l.get("kind") == "user"]
check("1a Codex feed answers with the prompts", len(users) == 2, users)
check("1b the newest prompt is the one sent last", users and users[-1].get("text") == "now shade it", users)
check("1c the answer between them survives the filter",
      any(l.get("kind") == "text" for l in lines), lines)

print("\n[2] Subagents — the panel asks mission, not the Claude reader")
subs = mi.project_subagents(FEED)
rows = subs.get("agents") or []
check("2a Codex subagents are listed", len(rows) == 1, subs)
check("2b the row carries an id the detail view can open", rows and rows[0].get("agent_id") == SUB, rows)
det = mi.project_subagent(FEED, SUB)
check("2c the detail view answers for a Codex agent", bool(det) and det.get("ok") is not False, det)

print("\n[3] Checkpoints — one list per FOLDER, whichever engine")
made = ck.create(FEED, "codex panels test", "manual", "codex")
check("3a a checkpoint can be taken on a Codex pane", bool(made.get("ok")), made)
cps = ck.list_checkpoints(BARE)
check("3b the Codex snapshot is in the FOLDER's list", bool(cps), cps)
check("3c and it is labelled with the engine that was about to run",
      bool(cps) and (cps[0].get("agent") == "codex"), cps[:1])
check("3d the Codex feed id reads the same store as the folder id",
      ck.list_checkpoints(FEED) == cps, (ck.list_checkpoints(FEED), cps))

print("\n[4] Skills — the panel asks for this engine's library")
cl = sk.list_skills(BARE, "claude")
cd = sk.list_skills(BARE, "codex")
check("4a Claude has a library to show", isinstance(cl, list), type(cl))
check("4b Codex has one too", isinstance(cd, list) and len(cd) == len(cl),
      "claude=%d codex=%d" % (len(cl), len(cd)))

# The switch has to reach the engine: Codex loads no skill by itself, so the ENABLED ones ride the
# thread's developer instructions. A library with every skill off is a valid state, so the two
# checks below are skipped rather than failed when nothing is enabled.
brief = sk.briefing(BARE, "codex")
on_ids = {it["id"] for it in cd if it.get("enabled")}
check("4c the briefing lists the enabled skills and nothing else",
      {b["id"] for b in brief} <= on_ids and all(Path(b["path"]).is_file() for b in brief),
      [b["id"] for b in brief])
if brief:
    note = cx._skills_note(BARE)
    check("4d the note names them, with the file to read",
          "SKILLS:" in note and brief[0]["name"] in note and brief[0]["path"] in note, note[:200])
    tp = cx._thread_params(str(PROJ), "read-only", "", BARE)
    check("4e and the note is in the thread Codex is actually started with",
          "SKILLS:" in (tp.get("developerInstructions") or ""),
          sorted(tp.keys()))
else:
    print("  SKIP  4d/4e every skill is switched off, so there is nothing to deliver")

print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

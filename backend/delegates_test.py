# -*- coding: utf-8 -*-
"""Work handed to ANOTHER engine, shown as the subagent it is.

Ask Claude for an image and it calls the Studio's job queue, which calls GPT. That is a
delegation, and the panels used to show nothing: the job was a row on the Jobs page that named
nobody. This test asks the three questions that have to hold for the panel to be trustworthy.

  1. attribution   delegates.owner_for  - who a job belongs to, and who it must NOT belong to
  2. the card      note_job/cards/merge - a job read back in the shape the panels speak
  3. the pane      detail               - one job opened on its own, and ids that are not jobs

No provider is called and no job is run: the queue is replaced by a stub, because what is under
test is the attribution and the card, not the generation.

Run:  backend/.venv/Scripts/python.exe backend/delegates_test.py
"""
import io
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_data = Path(tempfile.mkdtemp(prefix="delegates-data-"))
(_data / "tmp").mkdir(parents=True, exist_ok=True)
os.environ["ASSET_STUDIO_DATA"] = str(_data)

from asset_studio import delegates as dg        # noqa: E402
from asset_studio import mission as mi          # noqa: E402
from asset_studio import workspace as ws        # noqa: E402

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  PASS  %s" % name)
    else:
        fail += 1
        print("  FAIL  %s  %s" % (name, extra))


class FakeJob:
    """What the queue hands back, as much of it as a card reads."""

    def __init__(self, jid, status="running", out="", err="", cost=0.0, logs=()):
        self.id = jid
        self.stage = "image2d"
        self.provider_id = "openai-image"
        self.label = "snow leopard"
        self.params = {"prompt": "a snow leopard on a rock", "model": "gpt-image-1"}
        self.created_at = time.time() - 30
        self.started_at = time.time() - 28
        self.finished_at = 0.0
        self.status = type("S", (), {"value": status})()
        self.outputs = [type("A", (), {"path": out})()] if out else []
        self.error = err
        self.cost = cost
        self.logs = list(logs)
        self.step = "generate"
        self.progress = 0.5


JOBS: dict = {}
dg._job = lambda jid: JOBS.get(jid)          # the queue, stubbed: no provider is called

PROJ = Path(tempfile.mkdtemp(prefix="delegates-proj-"))
(PROJ / "main.py").write_text("print('hi')\n", encoding="utf-8")
ws.add_roots([str(PROJ)])
BARE = next(r["id"] for r in ws.roots() if Path(r["path"]).name == PROJ.name)
FEED = "codex--" + BARE

print("\n[1] Attribution — a job belongs to the agent that asked for it, or to nobody")
check("1a what the agent sends wins", dg.owner_for("claude-feed", "header-feed", False) == "claude-feed")
check("1b otherwise the MCP server's header", dg.owner_for("", "header-feed", False) == "header-feed")
check("1c a person pressing Generate is never an agent's delegation",
      dg.owner_for("", "", True) == "", dg.owner_for("", "", True))
check("1d and with nothing running, nothing is guessed",
      dg.owner_for("", "", False) in ("", None), dg.owner_for("", "", False))

print("\n[2] The card — a running job reads like a running agent")
JOBS["j1"] = FakeJob("j1")
dg.note_job(FEED, JOBS["j1"])
cards = dg.cards(FEED)
check("2a the job is on this conversation's list", len(cards) == 1, cards)
c = cards[0] if cards else {}
check("2b with an id the pane can open", c.get("agent_id") == "job-j1", c.get("agent_id"))
check("2c it says it is running", c.get("running") is True and c.get("open") is True, c)
check("2d it names the engine and the model", "image" in (c.get("description") or "")
      and "gpt-image-1" in (c.get("description") or ""), c.get("description"))
check("2e and it is marked as work sent elsewhere", c.get("source") == "studio", c.get("source"))
check("2f the prompt is kept, so the card can be audited",
      "snow leopard" in (c.get("prompt") or ""), c.get("prompt"))
check("2g another conversation does not see it", dg.cards("some-other-feed") == [])

print("\n[3] It finishes — the same card, with the file it wrote")
JOBS["j1"] = FakeJob("j1", status="succeeded", out=str(PROJ / "leopard.png"), cost=0.04)
JOBS["j1"].finished_at = time.time()
c = (dg.cards(FEED) or [{}])[0]
check("3a it stops spinning", c.get("running") is False, c)
check("3b the output file is the result", c.get("output_file", "").endswith("leopard.png"), c)
check("3c what it cost is shown", abs(float(c.get("cost") or 0) - 0.04) < 1e-9, c.get("cost"))

print("\n[4] The counts the rail and the composer read")
JOBS["j2"] = FakeJob("j2")
dg.note_job(FEED, JOBS["j2"])
by = dg.running_by_feed()
check("4a one entry per conversation, counting only what is in flight", by.get(FEED) == 1, by)
check("4b and never a second entry for the bare folder id", BARE not in by, by)
merged = dg.merge(FEED, {"agents": [{"agent_id": "task-1", "running": True, "open": True, "ts": 0}],
                         "running": 1, "open": 1})
check("4c the panel's own agents are kept", len(merged.get("agents") or []) == 3, merged.get("agents"))
check("4d the running total adds the job in flight", merged.get("running") == 2, merged)
check("4e the newest is first", (merged.get("agents") or [{}])[0].get("agent_id") == "job-j2",
      [a.get("agent_id") for a in merged.get("agents") or []])

print("\n[5] One job in its own pane")
JOBS["j2"].logs = ["queued behind 1 job", "calling openai-image"]
det = dg.detail(FEED, "job-j2")
check("5a it opens", bool(det) and det.get("ok") is True, det)
texts = [l.get("text") for l in (det or {}).get("lines") or []]
check("5b the prompt is the first thing in the timeline",
      texts and "snow leopard" in (texts[0] or ""), texts[:1])
check("5c the job's own log is the rest", any("openai-image" in (t or "") for t in texts), texts)
check("5d an id that is not a job of this conversation falls through",
      dg.detail(FEED, "task-1") is None and dg.detail(FEED, "job-nope") is None)

print("\n[6] The panel asks mission, and mission asks both")
subs = mi.project_subagents(FEED)
ids = [a.get("agent_id") for a in (subs.get("agents") or [])]
check("6a a Codex pane shows the delegated jobs", "job-j1" in ids and "job-j2" in ids, ids)
own = mi.project_subagent(FEED, "job-j2")
check("6b and opens one", bool(own) and own.get("ok") is True, own)
subs = mi.project_subagents(BARE)
check("6c a Claude pane in the same folder shows its own, not Codex's",
      "job-j1" not in [a.get("agent_id") for a in (subs.get("agents") or [])], subs.get("agents"))

print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)

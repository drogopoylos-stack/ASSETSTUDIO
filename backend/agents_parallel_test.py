# -*- coding: utf-8 -*-
"""Two agents on one project, and the costs that come with them — checked without a vendor call.

  * a "new chat" (or a switch to another conversation) must not kill a turn that is RUNNING —
    with two panes on one folder that turn belongs to the other pane. Claude and Codex both refuse.
  * a checkpoint frames the FOLDER: restoring one while another engine writes there is refused
    unless the caller confirms (`force`), and so is an edit-and-resend that restores files.
  * checkpoints v2: content stored once per hash, unchanged files not re-read, an unchanged folder
    not written twice, old blobs collected, version-1 files still readable.
  * a steer into a running turn takes no checkpoint.
  * BOOST's tool-output caps reach the Claude settings file and the Codex thread config.
  * one switch pauses every scheduled message.
  * DeepSeek pushes its live state over the WebSocket instead of being polled.

Run:  backend/.venv/Scripts/python.exe backend/agents_parallel_test.py
"""
import gzip
import json
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["ASSET_STUDIO_DATA"] = tempfile.mkdtemp(prefix="agents-parallel-test-")

from asset_studio import boost, cc_session, checkpoints, codex_app, deepseek_session, scheduled  # noqa: E402
from asset_studio.config import settings  # noqa: E402
from asset_studio.routers import mission as mission_router  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print("  PASS  " + name)
    else:
        failed += 1
        print("  FAIL  " + name + (("  -- " + str(detail)[:300]) if detail else ""))


PID = "d--parallel-fixture"

# --- 1. a running turn is not killed by "new chat" -----------------------------------------------
print("a running turn survives a second pane")


class _Proc:
    def poll(self):
        return None


busy_live = SimpleNamespace(alive=True, proc=_Proc(), session_id="sess-A", sig=None)
cc_session._live[PID] = busy_live
killed = []
with patch.object(cc_session, "_stream_busy", return_value=True), \
        patch.object(cc_session, "_agents_block", return_value=""), \
        patch.object(cc_session, "_kill_live", side_effect=lambda l: killed.append(l)), \
        patch.object(cc_session, "_spawn_live", side_effect=AssertionError("spawned")):
    r = cc_session._send_streaming(PID, "hello", tempfile.gettempdir(), "default", "acceptEdits",
                                   False, "default", "claude", True, "", "sess-A")
    check("Claude: new chat while a turn runs is refused", not r.get("ok") and r.get("busy"), r)
    check("...and the running session was not killed", not killed and cc_session._live.get(PID) is busy_live)
    r = cc_session._send_streaming(PID, "hello", tempfile.gettempdir(), "default", "acceptEdits",
                                   False, "default", "claude", False, "sess-OTHER", "sess-A")
    check("Claude: switching to another conversation while busy is refused", not r.get("ok") and r.get("busy"), r)
cc_session._live.pop(PID, None)

conv = codex_app._conv(PID)
conv.working, conv.turn_id, conv.thread_id, conv.cwd = True, "turn-1", "thread-1", tempfile.gettempdir()
calls = []
fake_srv = SimpleNamespace(request=lambda m, p, timeout=0: calls.append(m) or {}, loaded=set(), pid=1)
with patch.object(codex_app, "find_codex", return_value={"argv": ["codex"]}), \
        patch.object(codex_app, "server", return_value=fake_srv), \
        patch.object(codex_app, "_read_account", return_value={}), \
        patch.object(codex_app, "ready_to_chat", return_value=True):
    r = codex_app.send(PID, "start fresh", tempfile.gettempdir(), new_session=True)
    check("Codex: new chat while a turn runs is refused", not r.get("ok") and r.get("busy"), r)
    check("...and nothing was interrupted", "turn/interrupt" not in calls, calls)
    check("...and the turn is still marked working", conv.working)
conv.working = False

# --- 2. a restore is refused while another engine writes -------------------------------------------
print("restore vs. another agent")
with patch.object(codex_app, "is_sending", side_effect=lambda p: codex_app.bare(p) == PID), \
        patch.object(cc_session, "is_sending", return_value=False), \
        patch.object(deepseek_session, "is_sending", return_value=False):
    busy = cc_session.folder_busy(PID)
    check("folder_busy names the Codex feed working in the folder", busy == ["codex--" + PID], busy)
    check("...from a prefixed id too", cc_session.folder_busy("deepseek-harness--" + PID) == ["codex--" + PID])
    check("...and leaves out the caller's own feed",
          cc_session.folder_busy("codex--" + PID, exclude="codex--" + PID) == [])
    with patch.object(checkpoints, "restore", side_effect=AssertionError("restored")):
        r = mission_router.checkpoint_restore(PID, "123", mission_router.RestoreBody())
        check("the restore route refuses and says who is working",
              not r["ok"] and r.get("busy") and "Codex" in r["error"], r)
    with patch.object(checkpoints, "restore", return_value={"ok": True, "restored": 0}) as rs:
        r = mission_router.checkpoint_restore(PID, "123", mission_router.RestoreBody(force=True))
        check("...but goes ahead when the user confirmed (force)", r.get("ok") and rs.called, r)
    r = cc_session.rewind(PID, "uuid", "edited", restore_files=True)
    check("edit-and-resend with file restore is refused too", not r["ok"] and r.get("busy"), r)

# --- 3. checkpoints v2 ---------------------------------------------------------------------------
print("checkpoints v2")
root = Path(tempfile.mkdtemp(prefix="ckpt-root-"))
(root / "a.txt").write_text("alpha\n", encoding="utf-8")
(root / "b.txt").write_text("beta\n", encoding="utf-8")
CK = "d--ckpt-fixture"
with patch.object(checkpoints, "_proj_root", return_value=root.resolve()), \
        patch.object(checkpoints.workspace, "_within", return_value=True):
    c1 = checkpoints.create(CK, label="one")
    d = checkpoints._dir(CK)
    blobs = list((d / "blobs").glob("*/*.gz"))
    check("first checkpoint stores one blob per file", c1["ok"] and len(blobs) == 2, (c1, blobs))
    with gzip.open(d / f"{c1['id']}.json.gz", "rt", encoding="utf-8") as fh:
        stored = json.load(fh)
    check("...and the checkpoint itself holds hashes, not text",
          stored.get("v") == 2 and "files" not in stored and len(stored["refs"]) == 2)

    real_read = Path.read_text
    reads = []

    def counting(self, *a, **k):
        reads.append(self.name)
        return real_read(self, *a, **k)

    with patch.object(Path, "read_text", counting):
        c2 = checkpoints.create(CK, label="two")
    check("an unchanged folder is not written again", c2.get("unchanged") and c2["id"] == c1["id"], c2)
    check("...and no unchanged file was read again", not [n for n in reads if n.endswith(".txt")], reads)

    time.sleep(0.02)
    (root / "a.txt").write_text("alpha changed\n", encoding="utf-8")
    c3 = checkpoints.create(CK, label="three")
    blobs = list((d / "blobs").glob("*/*.gz"))
    check("a changed file adds exactly one blob", c3["ok"] and not c3.get("unchanged") and len(blobs) == 3,
          (c3, len(blobs)))
    df = checkpoints.diff(CK, c1["id"])
    check("diff against the first checkpoint shows the one modified file",
          df["ok"] and [c["path"] for c in df["changes"]] == ["a.txt"]
          and df["changes"][0]["status"] == "modified", df)
    r = checkpoints.restore(CK, c1["id"])
    check("restore puts the old text back", r["ok"] and (root / "a.txt").read_text() == "alpha\n", r)

    # a version-1 file, as the old code wrote it, still loads and restores
    v1 = {"id": "1000", "ts": 1.0, "label": "old", "kind": "turn", "agent": "", "root": str(root.resolve()),
          "partial": False, "file_count": 1, "files": {"b.txt": "beta v1\n"}}
    with gzip.open(d / "1000.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(v1, fh)
    os.utime(d / "1000.json.gz", (1, 1))
    r = checkpoints.restore(CK, "1000")
    check("a version-1 checkpoint still restores", r["ok"] and (root / "b.txt").read_text() == "beta v1\n", r)

    # prune + garbage collection
    with patch.object(checkpoints, "_KEEP", 1):
        (root / "a.txt").write_text("alpha final\n", encoding="utf-8")
        (root / "b.txt").write_text("beta final\n", encoding="utf-8")
        c4 = checkpoints.create(CK, label="four")
    left = sorted(p.name for p in d.glob("*.json.gz"))
    blobs = list((d / "blobs").glob("*/*.gz"))
    check("prune keeps only the newest checkpoint", left == [f"{c4['id']}.json.gz"], left)
    check("...and the blobs nobody points at are deleted", len(blobs) == 2, len(blobs))

# --- 4. a steer takes no checkpoint ------------------------------------------------------------------
print("checkpoint on send")
shots = []
with patch.object(cc_session, "_snapshot_before_turn", side_effect=lambda *a, **k: shots.append(a)), \
        patch.object(cc_session, "_send_codex", return_value={"ok": True}), \
        patch("asset_studio.spend.over_cap", return_value=""):
    with patch.object(codex_app, "is_sending", return_value=True):
        cc_session.send(PID, "also do this", agent="codex")
    check("a message steering a running Codex turn takes no checkpoint", not shots, shots)
    with patch.object(codex_app, "is_sending", return_value=False):
        cc_session.send(PID, "/compact", agent="codex")
        check("/compact takes no checkpoint", not shots, shots)
        cc_session.send(PID, "start the work", agent="codex")
    check("a message that starts a turn does", len(shots) == 1, shots)

# --- 5. BOOST tool-output caps ------------------------------------------------------------------------
print("BOOST tool-output caps")
settings.update({"boost": False})
cfg = json.loads(cc_session._btw_settings_file(PID).read_text(encoding="utf-8"))
check("BOOST off: the Claude settings keep the CLI default", "bashOutputMaxChars" not in cfg)
check("BOOST off: Codex threads keep their default",
      "tool_output_token_limit" not in (codex_app._thread_params("C:/x", "full", "").get("config") or {}))
settings.update({"boost": True})
cfg = json.loads(cc_session._btw_settings_file(PID).read_text(encoding="utf-8"))
check("BOOST on: Claude spills long command output to a file past 12000 chars",
      cfg.get("bashOutputMaxChars") == 12000, cfg.get("bashOutputMaxChars"))
check("BOOST on: Codex threads cap one tool output",
      (codex_app._thread_params("C:/x", "full", "").get("config") or {}).get("tool_output_token_limit") == 8000)
settings.update({"boost_bash_chars": 10})
check("the Claude cap is clamped to the CLI's own floor", boost.bash_output_chars() == 4000)
settings.update({"boost": False, "boost_bash_chars": 12000})

sent = []


def fake_request(method, params, timeout=0):
    sent.append(dict(params))
    if "tool_output_token_limit" in (params.get("config") or {}):
        raise codex_app.RpcError(-32600, "unknown field tool_output_token_limit")
    return {"ok": True}


srv = SimpleNamespace(request=fake_request)
r = codex_app._open_thread(srv, "thread/start",
                           {"cwd": "x", "config": {"mcp_servers": {"studio": {}}, "tool_output_token_limit": 8000}})
check("a Codex that refuses the cap still gets the Studio tool server",
      r == {"ok": True} and sent[-1].get("config") == {"mcp_servers": {"studio": {}}}, sent)

# --- 6. one switch for every scheduled message ----------------------------------------------------
print("scheduled messages")
check("not paused by default", scheduled.paused() is False)
r = mission_router.scheduled_pause(mission_router.SchedulePause(paused=True))
check("the route pauses them", r == {"paused": True} and scheduled.paused())
check("...and the list says so", mission_router.scheduled_list().get("paused") is True)
mission_router.scheduled_pause(mission_router.SchedulePause(paused=False))
check("...and resumes them", scheduled.paused() is False)

# --- 7. DeepSeek pushes instead of being polled ------------------------------------------------
print("DeepSeek live push")
pushed = []
fake_bus = SimpleNamespace(subscriber_count=lambda: 1, publish_threadsafe=lambda ev: pushed.append(ev))
with patch("asset_studio.events.bus", fake_bus):
    st = {"pid": PID, "working": True, "started": time.time(), "activity": "Working"}
    deepseek_session._live[PID] = st
    deepseek_session._publish(st, force=True)
    deepseek_session._publish(st)                  # inside 60 ms: throttled
    check("a live state is pushed for the DeepSeek feed id",
          len(pushed) == 1 and pushed[0].data["project_id"] == deepseek_session.PREFIX + PID, pushed)
    deepseek_session._live.pop(PID, None)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)

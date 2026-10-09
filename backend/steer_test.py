"""Steering integration tests without vendor calls or the user's session data.

Run: backend/.venv/Scripts/python.exe backend/steer_test.py
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["ASSET_STUDIO_DATA"] = tempfile.mkdtemp(prefix="studio-steer-test-")

from asset_studio import cc_session as cc, codex_app as cx, live_notes, mission
from asset_studio.routers import mission as routes


class SteeringTests(unittest.TestCase):
    def setUp(self):
        self.pid = "d--steer-test"
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.note = Path(self.root.name) / "project.note"

    def hook(self, event="PreToolUse", transcript="main.jsonl"):
        run = subprocess.run(
            [sys.executable, str(Path(cc.__file__).with_name("btw_hook.py")), str(self.note)],
            input=json.dumps({"hook_event_name": event, "transcript_path": transcript}),
            text=True, encoding="utf-8", capture_output=True, check=True,
        )
        return json.loads(run.stdout) if run.stdout.strip() else {}

    def live(self):
        live = SimpleNamespace(alive=True, proc=SimpleNamespace(poll=lambda: None, pid=123),
                               sig=None, session_id="session-a", lock=threading.Lock(),
                               btw_pending=False, outstanding=0, turn_active=True)
        cc._live[self.pid] = live
        self.addCleanup(cc._live.pop, self.pid, None)
        return live

    def test_claude_live_steer_uses_hook_without_restart_or_stdin(self):
        live = self.live()
        with patch.object(cc, "_stream_busy", return_value=True), \
             patch.object(cc, "_btw_note_file", return_value=self.note), \
             patch.object(cc, "_kill_live", side_effect=AssertionError("killed")), \
             patch.object(cc, "_write_msg", side_effect=AssertionError("stdin queued")):
            result = cc._send_streaming(self.pid, "Use the blue theme", self.root.name,
                                        "changed-model", "acceptEdits", False, "high", "claude",
                                        False, "session-a", "session-a", steer=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["steer_live"])
        self.assertTrue(live.btw_pending)
        self.assertEqual(result["steer_mode"], "live")
        self.assertIn(cc.STEER_PREFIX, live_notes.peek(self.note))
        ctx = self.hook()["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Use the blue theme", ctx)
        self.assertIn("adjust course now", ctx)
        self.assertNotIn("Do NOT derail", ctx)
        self.assertEqual(self.hook("PostToolUse"), {})

    def test_steer_refuses_a_finished_claude_turn(self):
        self.live()
        with patch.object(cc, "_stream_busy", return_value=False), \
             patch.object(cc, "_spawn_live", side_effect=AssertionError("spawned")):
            result = cc._send_streaming(self.pid, "correction", self.root.name, "default",
                                        "acceptEdits", False, "default", "claude", False,
                                        "session-a", "session-a", steer=True)
        self.assertFalse(result["ok"])
        self.assertFalse(live_notes.peek(self.note))

    def test_two_hook_boundaries_deliver_exactly_once(self):
        live_notes.put(self.note, cc._steer_wrap("one correction"))
        with ThreadPoolExecutor(max_workers=2) as pool:
            outputs = list(pool.map(self.hook, ["PreToolUse", "PostToolUse"]))
        self.assertEqual(sum(bool(x) for x in outputs), 1)

    def test_claude_mailbox_failure_is_reported_without_queuing_or_restart(self):
        self.live()
        with patch.object(cc, "_stream_busy", return_value=True), \
             patch.object(live_notes, "put", side_effect=OSError("disk full")), \
             patch.object(cc, "_write_msg", side_effect=AssertionError("queued")), \
             patch.object(cc, "_spawn_live", side_effect=AssertionError("spawned")):
            result = cc._send_streaming(self.pid, "correction", self.root.name, "default",
                                        "acceptEdits", False, "default", "claude", False,
                                        "session-a", "session-a", steer=True)
        self.assertFalse(result["ok"])

    def test_crashed_claim_is_recovered(self):
        live_notes.put(self.note, cc._steer_wrap("survive hook crash"))
        folder = Path(str(self.note) + ".d")
        file = next(folder.glob("*.note"))
        file.rename(file.with_suffix(".consuming-crashed"))
        self.assertIn("survive hook crash", self.hook()["hookSpecificOutput"]["additionalContext"])

    def test_parallel_writers_and_consumers_do_not_lose_notes(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: live_notes.put(self.note, f"update-{i:03d}"), range(40)))
            outputs = list(pool.map(lambda _: live_notes.claim(self.note), range(8)))
        delivered = "\n\n".join(x for x in outputs if x).split("\n\n")
        self.assertEqual(sorted(delivered), [f"update-{i:03d}" for i in range(40)])
        self.assertEqual(live_notes.peek(self.note), "")

    def test_subagent_leaves_update_for_parent(self):
        live_notes.put(self.note, cc._steer_wrap("parent only"))
        self.assertEqual(self.hook(transcript="subagents/agent-123.jsonl"), {})
        self.assertIn("parent only", live_notes.peek(self.note))
        self.assertTrue(self.hook())

    def test_side_note_keeps_its_fyi_framing(self):
        live_notes.put(self.note, cc._btw_wrap("FYI green is available"))
        ctx = self.hook("PostToolUse")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("do NOT drop, restart, or reprioritize", ctx)
        self.assertNotIn("adjust course now", ctx)

    def test_feed_recovers_pre_and_post_hook_updates(self):
        for event in ("PreToolUse", "PostToolUse"):
            live_notes.put(self.note, cc._steer_wrap("visible correction"))
            output = self.hook(event)
            entry = {"attachment": {"hookEvent": event, "stdout": json.dumps(output)}}
            self.assertEqual(mission._btw_from_hook(entry), cc._steer_wrap("visible correction"))

    def test_legacy_note_still_delivers(self):
        self.note.write_text("older side-note", encoding="utf-8")
        ctx = self.hook()["hookSpecificOutput"]["additionalContext"]
        self.assertIn("LIVE side-note", ctx)
        self.assertIn("older side-note", ctx)
        self.assertEqual(live_notes.peek(self.note), "")

    def test_recovery_preserves_steering_and_discards_stale_notes(self):
        live_notes.put(self.note, cc._steer_wrap("recover me"))
        with patch.object(cc, "_btw_note_file", return_value=self.note):
            self.assertEqual(cc._btw_wrap(cc._claim_pending_btw(self.pid)), cc._steer_wrap("recover me"))
        self.note.write_text("stale", encoding="utf-8")
        os.utime(self.note, (time.time() - 3600, time.time() - 3600))
        self.assertEqual(live_notes.claim(self.note, max_age=1800), "")

    def test_idle_sweeper_retries_failed_delivery(self):
        live = self.live()
        note = cc._btw_note_file(self.pid)
        live_notes.put(note, cc._steer_wrap("retry me"))
        with patch.object(cc, "_BTW_SWEEP_SECS", 0), \
             patch.object(cc, "_stream_busy", return_value=False), \
             patch.object(cc, "_write_msg", return_value=False):
            cc._btw_sweep_once()
        self.assertIn("retry me", live_notes.peek(note))
        with patch.object(cc, "_BTW_SWEEP_SECS", 0), \
             patch.object(cc, "_stream_busy", return_value=False), \
             patch.object(cc, "_write_msg", return_value=True) as write:
            cc._btw_sweep_once()
        self.assertEqual(write.call_args.args[1], cc._steer_wrap("retry me"))
        self.assertFalse(live_notes.peek(note))
        self.assertFalse(live.btw_pending)

    def test_route_passes_steer_to_shared_send(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(routes.router)
        with TestClient(app) as client, patch.object(cc, "send", return_value={"ok": True}) as send:
            response = client.post(f"/api/mission/projects/{self.pid}/send",
                                   json={"message": "correction", "steer": True, "agent": "codex"})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["ok"])
        self.assertTrue(send.call_args.kwargs["steer"])

    def test_idle_unsupported_empty_and_new_chat_refused_before_side_effects(self):
        for kwargs in ({}, {"agent": "deepseek-harness"}, {"message": ""}, {"new_session": True}):
            opts = {"message": "correction", "steer": True, **kwargs}
            with patch.object(cc, "_engine_busy", return_value=False), \
                 patch.object(cc, "_snapshot_before_turn", side_effect=AssertionError("snapshot")):
                self.assertFalse(cc.send(self.pid, **opts)["ok"])

    def test_slash_steer_uses_adapter_without_checkpoint(self):
        with patch.object(cc, "_engine_busy", return_value=True), \
             patch.object(cc, "_snapshot_before_turn", side_effect=AssertionError("snapshot")), \
             patch.object(cc, "_send_codex", return_value={"ok": True}) as send:
            self.assertTrue(cc.send(self.pid, "/steer use blue", agent="codex", path=self.root.name)["ok"])
        self.assertEqual(send.call_args.args[1], "use blue")
        self.assertTrue(send.call_args.args[-1])

    def codex(self, request):
        conv = cx._conv(self.pid)
        conv.working, conv.thread_id, conv.turn_id = True, "thread-a", "turn-a"
        conv.planner = False
        conv.queue.clear()
        srv = SimpleNamespace(request=request)
        for name, value in (("find_codex", {}), ("server", srv), ("_read_account", {}), ("ready_to_chat", True)):
            p = patch.object(cx, name, return_value=value if name != "find_codex" else {"argv": ["codex"]})
            p.start()
            self.addCleanup(p.stop)
        return conv

    def test_codex_steers_current_turn_even_if_planner_setting_changed(self):
        calls = []
        conv = self.codex(lambda method, params, **kw: calls.append((method, params)) or {})
        with patch.object(cx, "_publish"), patch.object(cx, "_log") as log:
            result = cx.send(self.pid, "use blue", self.root.name, planner=True, steer=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["steer_mode"], "live")
        self.assertEqual(calls, [("turn/steer", {"threadId": "thread-a", "expectedTurnId": "turn-a",
                                               "input": [{"type": "text", "text": "use blue", "text_elements": []}]})])
        self.assertTrue(log.call_args.args[1]["steer"])
        self.assertFalse(conv.queue)

    def test_codex_failed_steer_does_not_claim_success_or_queue(self):
        def fail(*args, **kwargs):
            raise cx.RpcError(-32000, "turn finished")
        conv = self.codex(fail)
        result = cx.send(self.pid, "correction", self.root.name, steer=True)
        self.assertFalse(result["ok"])
        self.assertFalse(conv.queue)

    def test_codex_idle_steer_does_not_start_turn(self):
        conv = self.codex(lambda *a, **kw: self.fail("unexpected RPC"))
        conv.working = False
        with patch.object(cx, "_start_turn", side_effect=AssertionError("started turn")):
            self.assertFalse(cx.send(self.pid, "correction", self.root.name, steer=True)["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

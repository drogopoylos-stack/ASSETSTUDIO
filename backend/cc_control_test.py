"""A TOOL CALL THAT WAITS FOR THE HOST MUST NOT WAIT FOREVER.

Measured on 2026-11-04: the first Blender tool call of a turn sent a `control_request`
(a telemetry dialog of the MCP server). Nothing read that type of event, so the call waited for
8.5 hours, the user's messages stayed queued, and the chat showed the label of another tool.

These tests run the real `_live_reader` over the same event lines, with a stand-in process. Nothing
here starts the real CLI.
"""
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

os.environ["ASSET_STUDIO_DATA"] = tempfile.mkdtemp(prefix="cc-control-test-")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from asset_studio import cc_session as cc  # noqa: E402


ELICIT = {"type": "control_request", "request_id": "r1",
          "request": {"subtype": "elicitation", "mcp_server_name": "blender", "message": "Opt in?"}}


def heartbeat(tool, seconds):
    return {"type": "tool_progress", "tool_name": tool, "elapsed_time_seconds": seconds,
            "heartbeat": True}


class _Proc:
    def __init__(self):
        self.stdin = io.BytesIO()

    def poll(self):
        return None


class _Base(unittest.TestCase):
    def setUp(self):
        self.live = cc._Live("g--TEST", _Proc(), None, ("claude",), "s1")
        self.live.alive = True
        self._tail, self._pub = cc._tail_lines, cc._publish_live
        cc._publish_live = lambda *a, **k: None
        self.addCleanup(self._restore)

    def _restore(self):
        cc._tail_lines, cc._publish_live = self._tail, self._pub

    def read(self, *events):
        cc._tail_lines = lambda live: iter(json.dumps(e).encode("utf-8") for e in events)
        cc._live_reader(self.live)

    def written(self):
        return [json.loads(x) for x in self.live.proc.stdin.getvalue().splitlines()]


class ControlRequestTest(_Base):
    def test_an_mcp_question_is_declined(self):
        self.read(ELICIT)
        self.assertEqual(self.written(), [{"type": "control_response", "response": {
            "subtype": "success", "request_id": "r1", "response": {"action": "decline"}}}])

    def test_an_unknown_request_gets_an_error_and_not_silence(self):
        self.read({"type": "control_request", "request_id": "r2", "request": {"subtype": "new_thing"}})
        res = self.written()[0]["response"]
        self.assertEqual((res["subtype"], res["request_id"]), ("error", "r2"))
        self.assertIn("new_thing", res["error"])

    def test_the_answer_goes_through_the_keeper_inbox(self):
        with tempfile.TemporaryDirectory() as d:
            self.live.inbox = d
            old, cc._WRITE_ACK = cc._WRITE_ACK, 0.0
            try:
                self.read(ELICIT)
            finally:
                cc._WRITE_ACK = old
            msgs = list(Path(d).glob("*.msg"))
            self.assertEqual(len(msgs), 1)
            sent = json.loads(msgs[0].read_text(encoding="utf-8"))
            self.assertEqual(sent["response"]["request_id"], "r1")
            self.assertEqual(self.live.proc.stdin.getvalue(), b"", "only the keeper holds stdin")

    def test_a_request_with_no_id_cannot_stop_the_reader(self):
        self.read({"type": "control_request"}, ELICIT)
        self.assertEqual(len(self.written()), 1, "the event after it must still be read")

    def test_a_dead_pipe_cannot_stop_the_reader(self):
        self.live.proc.stdin.close()
        self.read(ELICIT, heartbeat("Bash", 600))
        self.assertIn("Bash", self.live.activity)

    def test_the_lock_is_free_during_delivery_to_the_keeper(self):
        # The reader must not hold the lock across `_inbox_put`: the chat reads it for every update.
        seen = []

        def from_another_thread():
            free = self.live.lock.acquire(timeout=1)
            if free:
                self.live.lock.release()
            seen.append(free)

        def put(live, data):
            t = threading.Thread(target=from_another_thread)
            t.start()
            t.join()
            return True

        old = cc._inbox_put
        cc._inbox_put = put
        self.addCleanup(lambda: setattr(cc, "_inbox_put", old))
        self.live.inbox = "somewhere"
        self.read(ELICIT)
        self.assertEqual(seen, [True])


class ToolWaitLabelTest(_Base):
    def test_a_long_call_is_named_with_its_age(self):
        self.read(heartbeat("mcp__blender__get_addon_status", 30839))
        self.assertIn("blender · get_addon_status", self.live.activity)
        self.assertIn("513 minutes", self.live.activity)
        self.assertIn("Stop", self.live.activity, "it must say what to do about it")

    def test_a_short_call_keeps_its_own_label(self):
        self.live.activity = "Searching puppets"
        self.read(heartbeat("Grep", 30))
        self.assertEqual(self.live.activity, "Searching puppets")

    def test_a_helper_agent_is_not_reported_as_stuck(self):
        self.live.activity = "Thinking"
        self.read(heartbeat("Agent", 3000), heartbeat("Monitor", 3000))
        self.assertEqual(self.live.activity, "Thinking")

    def test_the_next_block_replaces_the_label(self):
        self.read(heartbeat("Bash", 600), {"type": "stream_event", "event": {
            "type": "content_block_start", "content_block": {"type": "thinking"}}})
        self.assertNotIn("Bash", self.live.activity)

    def test_a_bad_heartbeat_cannot_stop_the_reader(self):
        self.read({"type": "tool_progress", "tool_name": "Bash", "elapsed_time_seconds": "soon"}, ELICIT)
        self.assertEqual(len(self.written()), 1)

    def test_the_label_never_ends_the_session(self):
        with self.live.lock:
            self.live.turn_active = True
        cc._note_tool_wait(self.live, heartbeat("Bash", 99999))
        self.assertTrue(self.live.alive)
        self.assertTrue(self.live.turn_active)


if __name__ == "__main__":
    unittest.main()

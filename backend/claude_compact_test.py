"""Claude context meter regression tests, isolated from accounts and vendor calls."""
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

_data = tempfile.TemporaryDirectory(prefix="studio-compact-test-")
os.environ["ASSET_STUDIO_DATA"] = _data.name
from asset_studio import cc_session as cc, hook_events as he, mission


def assistant(tokens, timestamp=""):
    return {"type": "assistant", "timestamp": timestamp,
            "message": {"model": "claude-opus-5-5", "usage": {
                "input_tokens": tokens, "output_tokens": 10, "cache_read_input_tokens": 0}}}


class ClaudeCompactTests(unittest.TestCase):
    def setUp(self):
        self.pid = "test-project"
        self.ledger = he.file_for(self.pid)
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.unlink(missing_ok=True)
        self.prefs = patch.object(mission.settings, "get", side_effect=lambda k, d=None: False if k == "cc_1m" else d)
        self.prefs.start()
        self.addCleanup(self.prefs.stop)

    def hooks(self, rows):
        self.ledger.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        he._cache.clear()

    def test_output_only_updates_preserve_last_measured_input(self):
        info = mission._context_info([assistant(120000), assistant(0)])
        self.assertEqual(info["ctx_used"], 120000)
        self.assertEqual(info["ctx_pct"], 60)

    def test_system_boundary_drops_precompact_usage_without_another_turn(self):
        info = mission._context_info([assistant(500000),
            {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "manual"}},
            {"type": "user", "message": {"content": "Summary of the work"}}, assistant(0)])
        self.assertTrue(info["just_compacted"])
        self.assertLess(info["ctx_used"], 30000)
        self.assertEqual(info["ctx_max"], 200000)

    def test_summary_boundary_remains_supported(self):
        info = mission._context_info([assistant(190000),
            {"type": "user", "isCompactSummary": True, "message": {"content": "S" * 20000}}])
        self.assertTrue(info["just_compacted"])
        self.assertGreater(info["ctx_used"], 29000)
        self.assertLess(info["ctx_used"], 31000)

    def test_hook_completes_an_empty_meter_without_waiting_for_input(self):
        self.hooks([{"event": "PostCompact", "session": "active", "at": time.time(),
                     "summary_chars": 20000, "trigger": "manual"}])
        info = {"compacting": True}
        mission._apply_compact_hook(info, self.pid, session="active")
        self.assertEqual(info["ctx_used"], 30000)
        self.assertEqual(info["ctx_max"], 200000)
        self.assertEqual(info["ctx_pct"], 15)
        self.assertFalse(info["compacting"])
        self.assertTrue(info["just_compacted"])

    def test_other_session_and_subagent_cannot_change_this_meter(self):
        self.hooks([{"event": "PreCompact", "session": "other", "at": time.time()},
                    {"event": "PreCompact", "session": "active", "agent": "sub", "at": time.time()}])
        info = {"ctx_used": 100000}
        mission._apply_compact_hook(info, self.pid, session="active")
        self.assertEqual(info, {"ctx_used": 100000})

    def test_fresh_measurement_wins_over_hook_even_when_larger(self):
        ended = time.time() - 10
        self.hooks([{"event": "PostCompact", "session": "active", "at": ended,
                     "summary_chars": 10000}])
        info = mission._context_info([assistant(90000, datetime.now(timezone.utc).isoformat())])
        mission._apply_compact_hook(info, self.pid, session="active")
        self.assertEqual(info["ctx_used"], 90000)
        self.assertFalse(info["just_compacted"])

    def test_precompact_cache_metrics_are_cleared_after_hook_correction(self):
        self.hooks([{"event": "PostCompact", "session": "active", "at": time.time(),
                     "summary_chars": 20000}])
        info = mission._context_info([assistant(500000)])
        mission._apply_compact_hook(info, self.pid, session="active")
        self.assertEqual(info["ctx_max"], 200000)
        self.assertNotIn("cache_pct", info)

    def test_busy_compact_is_refused_before_delivery_or_restart(self):
        live = SimpleNamespace(alive=True, proc=SimpleNamespace(poll=lambda: None))
        with patch.dict(cc._live, {self.pid: live}), patch.object(cc, "_stream_busy", return_value=True), \
             patch.object(cc, "_write_msg", side_effect=AssertionError("delivered")), \
             patch.object(cc, "_kill_live", side_effect=AssertionError("restarted")):
            result = cc._send_streaming(self.pid, "/compact", _data.name, "default", "acceptEdits",
                                        False, "default", "claude", False, "", "")
        self.assertFalse(result["ok"])
        self.assertTrue(result["busy"])


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        _data.cleanup()

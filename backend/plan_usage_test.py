"""Plan/context and spend regression tests. No vendor requests or user data writes."""
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_data = tempfile.TemporaryDirectory(prefix="studio-plan-test-")
os.environ["ASSET_STUDIO_DATA"] = _data.name

from asset_studio import cc_session as cc, codex_app as cx, mission, spend, usage


class PlanUsageTests(unittest.TestCase):
    def setUp(self):
        self.day = datetime.now().strftime("%Y-%m-%d")
        spend._state = None
        spend._FILE.unlink(missing_ok=True)
        usage.invalidate()

    def test_refresh_invalidates_cached_plan(self):
        with patch.object(usage, "_creds", return_value={"subscriptionType": "Pro"}):
            self.assertEqual(usage.plan_tier(), "pro")
        with patch.object(usage, "_creds", return_value={"subscriptionType": "Max"}):
            self.assertEqual(usage.plan_tier(), "pro")
            usage.invalidate()
            self.assertEqual(usage.plan_tier(), "max")

    def test_oauth_plan_does_not_exempt_other_provider(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(usage, "on_subscription", return_value=True):
            self.assertFalse(cc._claude_billed("d--project"))
            self.assertTrue(cc._claude_billed(mission.KIMI_PREFIX + "d--project"))
            self.assertTrue(cc._claude_billed(mission.QWEN_PREFIX + "d--project"))
            for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                        "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
                with self.subTest(key=key), patch.dict(os.environ, {key: "configured"}):
                    self.assertTrue(cc._claude_billed("d--project"))

    def test_unknown_auth_is_conservatively_api_priced(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(usage, "on_subscription", return_value=False):
            self.assertTrue(cc._claude_billed("d--project"))
        with patch.object(usage, "on_subscription", side_effect=OSError()):
            self.assertTrue(cc._claude_billed("d--project"))

    def test_cli_default_is_preserved_on_all_plans(self):
        with patch.object(cc.settings, "get", return_value=True):
            for model in ("", "default"):
                self.assertEqual(cc._apply_1m(model), model)

    def test_native_models_are_bounded_and_existing_usage_is_preserved(self):
        def setting(key, default=None):
            return False if key == "cc_1m" else default
        with patch.object(cc.settings, "get", side_effect=setting):
            for model in ("claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5-5", "opus[1m]"):
                with self.subTest(model=model):
                    self.assertEqual(mission.model_window(model), 200_000)
                    self.assertEqual(cc._context_window_note(model), "")
                    self.assertEqual(mission.model_window(model, 400_000), 1_000_000)
            self.assertEqual(mission.model_window("kimi-k3"), 1_000_000)

    def test_stream_launch_sets_limit_only_for_claude(self):
        def setting(key, default=None):
            return False if key in ("cc_1m", "cc_phases") else default
        proc = SimpleNamespace(pid=123, poll=lambda: None)
        with patch.object(cc.settings, "get", side_effect=setting), \
             patch.object(cc, "_claim_pending_btw", return_value=""), \
             patch.object(cc, "_resolve_node_shim", return_value=None), \
             patch.object(cc, "_mcp_on", return_value=False), \
             patch.object(cc, "_start_keeper", return_value=(None, None, None)), \
             patch.object(cc, "_register"), patch.object(cc, "_start_reaper"), \
             patch.object(cc.threading, "Thread"), \
             patch.object(cc.subprocess, "Popen", return_value=proc) as spawn, \
             patch.object(cc, "_alt_env", return_value={"ANTHROPIC_MODEL": "kimi-k3"}):
            for pid, limited in (("d--project", True), (mission.KIMI_PREFIX + "d--project", False)):
                with patch.dict(os.environ, {}, clear=True):
                    live = cc._spawn_live(pid, ["claude"], _data.name, ("claude", _data.name, "default"))
                self.addCleanup(live.logf.close)
                self.assertEqual(spawn.call_args.kwargs["env"].get("CLAUDE_CODE_DISABLE_1M_CONTEXT") == "1", limited)

    def test_context_switch_changes_idle_session_signature(self):
        with patch.object(cc.settings, "get", return_value=True):
            before = cc._note_sig()
        with patch.object(cc.settings, "get", side_effect=lambda k, d=True: False if k == "cc_1m" else True):
            self.assertNotEqual(before, cc._note_sig())

    def test_subscription_totals_are_separate_and_survive_reload(self):
        spend.record("claude-project", "opus", 100, billed=False)
        spend.record("api-project", "deepseek", 2, billed=True)
        spend._state = None
        self.assertEqual(spend.month_to_date(), 2)
        self.assertEqual(spend.month_to_date(billed_only=False), 102)
        with patch.object(spend.settings, "get", return_value=3):
            self.assertFalse(spend.cap_state(102)["over"])
        spend.record("api-project", "deepseek", 1, billed=True)
        with patch.object(spend.settings, "get", return_value=3):
            self.assertTrue(spend.cap_state()["over"])

    def test_old_unclassified_history_is_preserved_and_reported(self):
        spend._FILE.write_text(json.dumps({"projects": {}, "days": {self.day: 50},
                                         "billed_days": {self.day: 2}}), encoding="utf-8")
        self.assertEqual(spend.cap_state()["unclassified"], 48)
        spend.record("new-plan-turn", "opus", 10, billed=False)
        spend._state = None
        self.assertEqual(spend.cap_state()["unclassified"], 48)
        self.assertEqual(spend.month_to_date(), 2)
        self.assertEqual(spend.month_to_date(False), 60)

    def test_codex_completed_turn_uses_its_own_billing_classification(self):
        for billed in (True, False):
            with self.subTest(billed=billed):
                conv = cx._Conv("codex:test")
                conv.model = "test-model"
                conv.billed = billed
                conv.usage["thread-test"] = {"last": {"inputTokens": 100, "outputTokens": 10}}
                with patch.object(cx, "_log"), patch.object(cx, "_index_update"), \
                     patch.object(cx, "_publish"), patch("asset_studio.pricing.cost_with_basis", return_value=(1, "list")), \
                     patch("asset_studio.turns.record"), patch.object(spend, "record") as bank:
                    cx._on_main_note(conv, "thread-test", "turn/completed", {"turn": {"id": "turn-test", "status": "completed"}})
                self.assertEqual(bank.call_args.kwargs["billed"], billed)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        _data.cleanup()

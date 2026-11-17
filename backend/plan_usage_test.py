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

    def test_explicit_1m_conversation_is_not_compact_capped(self):
        """A conversation started with an explicit "1M context" runs the whole window; any other
        Claude launch keeps the `cc_compact_window` cap (the economy default)."""
        def setting(key, default=None):
            if key == "cc_compact_window":
                return 400_000
            return False if key == "cc_phases" else (True if key == "cc_1m" else default)
        proc = SimpleNamespace(pid=123, poll=lambda: None)
        with patch.object(cc.settings, "get", side_effect=setting),              patch.object(cc, "_claim_pending_btw", return_value=""),              patch.object(cc, "_resolve_node_shim", return_value=None),              patch.object(cc, "_mcp_on", return_value=False),              patch.object(cc, "_start_keeper", return_value=(None, None, None)),              patch.object(cc, "_register"), patch.object(cc, "_start_reaper"),              patch.object(cc.threading, "Thread"),              patch.object(cc.subprocess, "Popen", return_value=proc) as spawn:
            for full, cap in ((False, "400000"), (True, None)):
                with self.subTest(full=full), patch.dict(os.environ, {}, clear=True):
                    live = cc._spawn_live("d--project", ["claude"], _data.name,
                                          ("claude", _data.name, "default"), True, full)
                    self.addCleanup(live.logf.close)
                    self.assertEqual(spawn.call_args.kwargs["env"].get("CLAUDE_CODE_AUTO_COMPACT_WINDOW"), cap)
                    self.assertIs(live.want_full, full)

    def test_full_window_record_is_per_conversation(self):
        with tempfile.TemporaryDirectory() as d,                 patch.object(mission, "_FULL_F", Path(d) / "full.json"),                 patch.object(mission, "_full_cache", {"mtime": -1.0, "map": {}}):
            self.assertFalse(mission.full_window("p1", "s1"))
            mission.remember_full_window("p1", "s1")
            mission.remember_full_window("p1", "s1")          # add-only, no duplicate
            self.assertTrue(mission.full_window("p1", "s1"))
            self.assertFalse(mission.full_window("p1", "s2"))
            self.assertFalse(mission.full_window("p2", "s1"))
            self.assertFalse(mission.full_window("p1", ""))
            self.assertEqual(json.loads((Path(d) / "full.json").read_text())["p1"], ["s1"])

    def test_context_switch_does_not_respawn_idle_session(self):
        """D6: the 1M window belongs to the CONVERSATION, so moving the global switch must not
        change the idle-session signature.

        This test used to assert the opposite. A signature change respawns the CLI, which re-sends
        the whole conversation under a NEW prefix — a prompt-cache WRITE where the same request would
        have been a read, about $6.50 on an 800k conversation. The switch now sets only the default
        for the NEXT new conversation, so there is no signature difference left to detect."""
        with patch.object(cc.settings, "get", return_value=True):
            before = cc._note_sig(True)
        with patch.object(cc.settings, "get", side_effect=lambda k, d=True: False if k == "cc_1m" else True):
            self.assertEqual(before, cc._note_sig(True))
        # a conversation that chose 200k holds that too
        self.assertEqual(cc._note_sig(False), cc._note_sig(False))
        # AND THE LENGTH IS PART OF THE CONTRACT. An inherited session's stored signature is
        # compared positionally, so a tuple of a different size would differ from every adopted
        # session's record and respawn it for a change the user never made.
        self.assertEqual(len(cc._note_sig(True)), 13)

    def test_window_is_per_conversation_not_global(self):
        """D6: a conversation keeps the window it started with; the global is only a default."""
        with tempfile.TemporaryDirectory() as d, \
                patch.object(mission, "_WINDOW_F", Path(d) / "window.json"), \
                patch.object(mission, "_window_cache", {"mtime": -1.0, "map": {}}):
            # recorded once, read back
            mission.remember_window("p1", "s1", False)
            self.assertIs(mission.window_for("p1", "s1"), False)
            # the global moving does NOT re-window a live conversation
            with patch.object(cc.settings, "get", return_value=True):
                self.assertFalse(cc._resolve_window("p1", "s1", False))
            # ...but a new conversation takes the current default
            with patch.object(cc.settings, "get", return_value=True):
                self.assertTrue(cc._resolve_window("p1", "", True))
            # a conversation from before this feature adopts today's setting, then holds it
            with patch.object(cc.settings, "get", return_value=False):
                self.assertFalse(cc._resolve_window("p1", "s2", False))
            with patch.object(cc.settings, "get", return_value=True):
                self.assertFalse(cc._resolve_window("p1", "s2", False))
            self.assertIs(mission.window_for("p1", "s2"), False)
            # an unknown conversation is not claimed to have a choice
            self.assertIsNone(mission.window_for("p1", "never-seen"))
            self.assertIsNone(mission.window_for("p1", ""))

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

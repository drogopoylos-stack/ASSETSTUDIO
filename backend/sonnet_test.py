"""Pinned Sonnet selection, pricing, and Opus -> Sonnet delegation launch wiring.

No API requests or real settings writes. Run: python -m unittest sonnet_test -v
"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from asset_studio import cc_session as cs, mission, pricing


def value(args, flag):
    return args[args.index(flag) + 1]


class SonnetTests(unittest.TestCase):
    def test_picker_and_rate_card(self):
        self.assertIn("claude-sonnet-5-5", cs.status()["models"])
        self.assertIn("sonnet", cs.MODELS)
        for model in ("claude-sonnet-5-5", "claude-sonnet-5-5[1m]",
                      "us.anthropic.claude-sonnet-5-5-20260928"):
            with self.subTest(model=model):
                self.assertEqual(pricing.tier_for(model), "tier_2_10_cr010")
                self.assertEqual(pricing.cost(model, {"cache_read": 1_000_000}), 0.1)
                self.assertAlmostEqual(pricing.cost(model, {
                    "input": 1_000_000, "output": 1_000_000,
                    "cache_write": 1_000_000, "cache_read": 1_000_000,
                }), 14.6)
        self.assertEqual(pricing.cost("claude-sonnet-5", {"cache_read": 1_000_000}), 0.2)

    def test_native_context_respects_switch(self):
        with patch.object(mission.settings, "get", side_effect=lambda k, d=None: True if k == "cc_1m" else d):
            self.assertEqual(mission.model_window("claude-sonnet-5-5"), 1_000_000)
        with patch.object(mission.settings, "get", side_effect=lambda k, d=None: False if k == "cc_1m" else d):
            self.assertEqual(mission.model_window("claude-sonnet-5-5"), 200_000)

    def build(self, stream, model, project=""):
        with patch.object(cs.settings, "get", return_value=False), \
                patch.object(cs, "_snapshot_flag", return_value=[]), \
                patch.object(cs, "_btw_settings_file", return_value=Path("unused-settings.json")):
            if stream:
                return cs._claude_stream_args("claude", None, model, "default", False,
                                              "default", project)
            return cs._claude_args("claude", "hello", None, model, "default", False,
                                   "default", project)

    def test_opus_keeps_its_model_and_gets_pinned_worker(self):
        for stream in (False, True):
            for parent in ("claude-opus-5-5[1m]", "opus"):
                with self.subTest(stream=stream, parent=parent):
                    args = self.build(stream, parent)
                    self.assertEqual(value(args, "--model"), parent)
                    workers = json.loads(value(args, "--agents"))
                    worker = workers["studio-sonnet"]
                    self.assertEqual(worker["model"], "claude-sonnet-5-5")
                    self.assertIn("only when", worker["description"])
                    self.assertNotIn("permissionMode", worker)
                    note = value(args, "--append-system-prompt")
                    self.assertIn("subagent_type='studio-sonnet'", note)
                    self.assertIn("Omit the per-call model", note)

    def test_default_and_sonnet_parent_can_also_delegate(self):
        for model in ("default", "claude-sonnet-5-5"):
            args = self.build(True, model)
            self.assertIn("--agents", args)
            if model == "default":
                self.assertNotIn("--model", args)
            else:
                self.assertEqual(value(args, "--model"), model)

    def test_other_providers_do_not_get_anthropic_worker(self):
        for project in ("kimi--test", "qwen--test"):
            for stream in (False, True):
                with self.subTest(project=project, stream=stream):
                    args = self.build(stream, "default", project)
                    self.assertNotIn("--agents", args)
                    self.assertNotIn("SONNET DELEGATION", " ".join(args))


if __name__ == "__main__":
    unittest.main()

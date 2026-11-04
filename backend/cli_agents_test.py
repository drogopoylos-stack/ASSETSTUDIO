"""A model CLI the agent starts in the BACKGROUND is a running card until its notification.

Claude Code answers a `run_in_background` Bash call at once with a receipt; the work ends later
with a `<task-notification>` naming the same tool_use id. Read as the end, the receipt closed the
card a third of a second after GPT started drawing.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from asset_studio import cli_agents  # noqa: E402

CMD = ("cd /c/tmp/kapow-art/lucho && codex exec --skip-git-repo-check --sandbox workspace-write "
       "-C . -i 1-base.png - < prompt.txt > run.log 2>&1; echo \"exit $?\"")


def _iso(at: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(at))


class BackgroundCodex(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.out = self.root / "btd1sm2nb.output"
        self.out.write_text("exit 0\nlucho-draft.png\n", encoding="utf-8")
        self.file = self.root / "sess.jsonl"
        self._orig = cli_agents._session_file
        cli_agents._session_file = lambda pid, session="": self.file
        self.t0 = time.time() - 30

    def tearDown(self):
        cli_agents._session_file = self._orig
        self.dir.cleanup()

    def _write(self, *records):
        with self.file.open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")

    def _use(self, background=True):
        inp = {"command": CMD, "description": "Generate the Lucho draft with Codex", "timeout": 1200000}
        if background:
            inp["run_in_background"] = True
        return {"type": "assistant", "timestamp": _iso(self.t0),
                "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "toolu_A", "name": "Bash", "input": inp}]}}

    def _receipt(self):
        text = ("Command running in background with ID: btd1sm2nb. Output is being written to: "
                f"{self.out}. You will be notified when it completes.")
        return self._result(text, self.t0 + 0.3)

    def _result(self, text, at):
        return {"type": "user", "timestamp": _iso(at),
                "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_A", "content": text}]}}

    def _note(self, status="completed", code=0):
        text = ("<task-notification>\n<task-id>btd1sm2nb</task-id>\n<tool-use-id>toolu_A</tool-use-id>\n"
                f"<output-file>{self.out}</output-file>\n<status>{status}</status>\n"
                f"<summary>Background command \"Generate the Lucho draft with Codex\" {status} "
                f"(exit code {code})</summary>\n</task-notification>")
        return {"type": "user", "timestamp": _iso(self.t0 + 20),
                "message": {"role": "user", "content": text}}

    def _card(self):
        cs = cli_agents.cards("G--KAPOW")
        self.assertEqual(len(cs), 1)
        return cs[0]

    def test_the_receipt_alone_keeps_the_card_running(self):
        self._write(self._use(), self._receipt())
        c = self._card()
        self.assertTrue(c["running"])
        self.assertEqual(c["status"], "running")
        self.assertEqual(c["log_file"], str(self.out))
        self.assertIn("Lucho", c["description"])

    def test_the_notification_ends_it_with_the_output(self):
        self._write(self._use(), self._receipt(), self._note())
        c = self._card()
        self.assertFalse(c["running"])
        self.assertEqual(c["status"], "succeeded")
        self.assertIn("lucho-draft.png", c["result"])

    def test_a_nonzero_exit_is_a_failure(self):
        self._write(self._use(), self._receipt(), self._note(code=1))
        self.assertEqual(self._card()["status"], "failed")

    def test_a_killed_task_stopped(self):
        self._write(self._use(), self._receipt(), self._note(status="killed", code=137))
        self.assertEqual(self._card()["status"], "stopped")

    def test_a_foreground_call_still_ends_at_its_result(self):
        self._write(self._use(background=False), self._result("exit 0", self.t0 + 25))
        c = self._card()
        self.assertFalse(c["running"])
        self.assertEqual(c["status"], "succeeded")

    def test_the_pane_rows_have_something_to_draw(self):
        # A tool row draws title and command, never text: the pane was a column of empty bullets.
        self._write(self._use(), self._receipt(), self._note())
        lines = cli_agents.detail("G--KAPOW", self._card()["agent_id"])["lines"]
        self.assertEqual(lines[0]["kind"], "tool")
        self.assertEqual(lines[0]["title"], "Bash")
        self.assertIn("codex exec", lines[0]["command"])
        self.assertEqual([r["text"] for r in lines if r["kind"] == "log"], ["exit 0", "lucho-draft.png"])
        for r in lines:
            if r["kind"] == "tool":
                self.assertTrue(r.get("title"))
            else:
                self.assertTrue(r.get("text"))

    def test_the_result_does_not_print_the_log_again(self):
        self._write(self._use(), self._receipt(), self._note())
        result = [r for r in cli_agents.detail("G--KAPOW", self._card()["agent_id"])["lines"]
                  if r["kind"] == "result"]
        self.assertEqual(len(result), 1)
        self.assertIn("completed (exit code 0)", result[0]["text"])
        self.assertNotIn("lucho-draft.png", result[0]["text"])
        self.assertNotIn("ok", result[0])

    def test_a_failed_run_says_so_in_the_pane(self):
        self._write(self._use(), self._receipt(), self._note(code=1))
        lines = cli_agents.detail("G--KAPOW", self._card()["agent_id"])["lines"]
        self.assertIs(next(r for r in lines if r["kind"] == "result")["ok"], False)

    def test_the_result_stays_whole_without_a_log(self):
        self._write(self._use(), self._receipt(), self._note())
        cid = self._card()["agent_id"]
        self.out.unlink()
        self._write(self._use(background=False), self._result("exit 0\n\nsecond part", self.t0 + 25))
        lines = cli_agents.detail("G--KAPOW", cid)["lines"]
        self.assertFalse([r for r in lines if r["kind"] == "log"])
        self.assertIn("second part", next(r for r in lines if r["kind"] == "result")["text"])


class MixedTimestamps(unittest.TestCase):
    """A Task agent's ts is ISO text, a shell card's is epoch seconds. The merge sort compared
    the two, raised TypeError, and the agents icon showed nothing for that folder."""

    def test_merge_sorts_text_and_number_ts(self):
        now = time.time()
        task = {"id": "task-1", "ts": _iso(now - 60), "running": True}
        card = {"id": "cli-1", "ts": now, "running": True, "source": "cli"}
        orig = cli_agents.cards
        cli_agents.cards = lambda pid, session="": [card]
        try:
            out = cli_agents.merge("p", {"agents": [task], "running": 1, "open": 1})
        finally:
            cli_agents.cards = orig
        self.assertEqual([a["id"] for a in out["agents"]], ["cli-1", "task-1"])
        self.assertEqual(out["running"], 2)

    def test_epoch_of(self):
        self.assertEqual(cli_agents.epoch_of(5), 5.0)
        self.assertAlmostEqual(cli_agents.epoch_of(_iso(1000.0)), 1000.0, places=3)
        self.assertEqual(cli_agents.epoch_of(""), 0.0)
        self.assertEqual(cli_agents.epoch_of(None), 0.0)


if __name__ == "__main__":
    unittest.main()

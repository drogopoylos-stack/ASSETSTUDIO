import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from asset_studio import codex_runs as runs

THREAD = "01a0f3f9-088c-7043-96a1-aaf75cc5fd28"


class RunTests(unittest.TestCase):
    def test_history_survives_new_turn_and_reload(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(runs, "ROOT", Path(temp)), patch.object(runs, "import_images", return_value=[]):
            log = Path(temp) / "run.log"
            self.assertEqual(runs.inspect("unused", log), {})
            runs.prepare("p", log, message="First", images=["ref.png"], model="test")
            log.write_text('\n'.join(json.dumps(e) for e in [
                {"type": "thread.started", "thread_id": THREAD},
                {"type": "item.started", "item": {"id": "tool", "type": "command_execution", "status": "in_progress"}},
                {"type": "item.completed", "item": {"id": "tool", "type": "command_execution", "status": "completed"}},
                {"type": "item.completed", "item": {"id": "answer", "type": "agent_message", "text": "Answer one"}},
                {"type": "turn.completed"}]))
            self.assertEqual(runs.prepare("p", log, message="Second"), THREAD)
            log.write_text(json.dumps({"type": "thread.started", "thread_id": THREAD}) + '\n' + json.dumps({"type": "turn.failed", "error": {"message": "Offline"}}))
            state = runs.inspect("p", log)
            self.assertEqual(len(state["turns"]), 2)
            self.assertEqual(state["turns"][0]["reply"], "Answer one")
            self.assertEqual(state["turns"][0]["attachments"], ["ref.png"])
            self.assertEqual(len(state["turns"][0]["activities"]), 1)
            self.assertEqual(state["turns"][1]["error"], "Offline")
            self.assertEqual(runs.inspect("p", log), state)
            runs.prepare("p", log, fresh=True, message="New conversation")
            self.assertEqual(len(runs.load("p")["turns"]), 1)
            self.assertEqual(len(list(runs.folder("p").glob("conversation-*.json"))), 1)

    def test_clean_json_reply_and_error(self):
        text = "\n".join(json.dumps(e) for e in [
            {"type": "thread.started", "thread_id": THREAD},
            {"type": "item.completed", "item": {"id": "1", "type": "agent_message", "text": "Here is your image"}},
            {"type": "item.completed", "item": {"id": "2", "type": "command_execution", "aggregated_output": "secret diagnostic"}},
            {"type": "turn.failed", "error": {"message": "Network failed"}},
        ])
        parsed = runs.parse(text)
        self.assertEqual(parsed["thread_id"], THREAD)
        self.assertEqual(parsed["reply"], "Here is your image")
        self.assertEqual(parsed["error"], "Network failed")

    def test_legacy_reply_not_duplicated(self):
        parsed = runs.parse(f"session id: {THREAD}\nuser\ninternal prompt\ncodex\nHello\ntokens used\n42\nHello")
        self.assertEqual(parsed["reply"], "Hello")
        self.assertEqual(parsed["thread_id"], THREAD)

    def test_resume_is_scoped_and_new_chat_resets(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(runs, "ROOT", Path(temp)), patch.object(runs, "import_images", return_value=[]):
            log = Path(temp) / "run.log"
            log.write_text(json.dumps({"type": "thread.started", "thread_id": THREAD}))
            self.assertEqual(runs.prepare("project-a", log), THREAD)
            self.assertEqual(runs.load("project-a")["thread_id"], THREAD)
            self.assertEqual(runs.load("project-b"), {})
            self.assertEqual(runs.prepare("project-a", log, fresh=True), "")
            self.assertNotIn("thread_id", runs.inspect("project-a", log))

    def test_generated_images_are_copied_without_deleting_originals(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(runs, "ROOT", Path(temp) / "studio"), patch.dict("os.environ", {"CODEX_HOME": temp}):
            image = Path(temp) / "generated_images" / THREAD / "image.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"test image")
            result = runs.import_images("project-a", THREAD)
            self.assertEqual(len(result), 1)
            self.assertEqual(Path(result[0]["path"]).read_bytes(), b"test image")
            self.assertTrue(image.exists())
            self.assertEqual(runs.import_images("project-a", "../../other"), [])


if __name__ == "__main__":
    unittest.main()

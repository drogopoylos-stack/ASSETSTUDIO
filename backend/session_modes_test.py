"""session_modes: the approval mode a folder's agent runs with, the same from every device."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from asset_studio import session_modes as M  # noqa: E402


class Modes(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self._file = M._FILE
        M._FILE = Path(self.dir.name) / "session_modes.json"

    def tearDown(self):
        M._FILE = self._file
        self.dir.cleanup()

    def test_a_folder_with_no_mode_says_so(self):
        self.assertEqual(M.get("d--work-game", "claude"), "")

    def test_a_picked_mode_is_kept_per_folder_and_agent(self):
        self.assertTrue(M.put("d--work-game", "claude", "bypassPermissions"))
        self.assertEqual(M.get("d--work-game", "claude"), "bypassPermissions")
        self.assertEqual(M.get("d--work-game", "codex"), "")
        self.assertEqual(M.get("d--other", "claude"), "")

    def test_the_drive_letter_case_is_one_folder(self):
        M.put("D--work-game", "claude", "bypassPermissions")
        self.assertEqual(M.get("d--work-game", "claude"), "bypassPermissions")

    def test_a_send_with_no_mode_gets_the_saved_one(self):
        M.put("d--work-game", "claude", "bypassPermissions")
        self.assertEqual(M.resolve("d--work-game", "claude", ""), "bypassPermissions")
        self.assertEqual(M.resolve("d--work-game", "claude", "plan"), "plan")

    def test_with_nothing_saved_the_fallback_runs(self):
        self.assertEqual(M.resolve("d--new", "codex", "", "full"), "full")

    def test_bad_input_is_refused(self):
        self.assertFalse(M.put("", "claude", "bypassPermissions"))
        self.assertFalse(M.put("d--x", "claude", "rm -rf /"))
        self.assertEqual(M.get("d--x", "claude"), "")


if __name__ == "__main__":
    unittest.main()

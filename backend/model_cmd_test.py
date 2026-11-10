"""/gamemodel: the art tools are told only to a message that asks for them, 2D or 3D."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from asset_studio import cc_session  # noqa: E402
from asset_studio import model_cmd as M  # noqa: E402

B = "http://127.0.0.1:8777"


class GameModel(unittest.TestCase):
    def test_a_message_without_the_command_is_left_alone(self):
        self.assertEqual(M.apply("make a 3d sword", B), ("make a 3d sword", None))
        self.assertEqual(M.apply("please /gamemodel 3d", B)[1], None)

    def test_3d_gets_the_3d_tools_and_the_request(self):
        out, dim = M.apply("/gamemodel 3d a short sword with a wrapped grip", B)
        self.assertEqual(dim, "3d")
        self.assertIn("[/gamemodel 3D]", out)
        self.assertIn(B + "/api/polyhaven/search", out)
        self.assertIn("parts.<key>.maps", out)
        self.assertNotIn("[/gamemodel 2D]", out)
        self.assertTrue(out.endswith("a short sword with a wrapped grip"))
        self.assertFalse(out.lstrip().startswith("/gamemodel"))

    def test_2d_gets_the_2d_tools(self):
        out, dim = M.apply("/GameModel make a 2D coin that spins, 6 frames", B)
        self.assertEqual(dim, "2d")
        self.assertIn("[/gamemodel 2D]", out)
        self.assertIn(B + "/engine?draw=", out)
        self.assertNotIn("[/gamemodel 3D]", out)

    def test_the_words_are_read_loosely(self):
        self.assertEqual(M.dimension("a 3-D helmet"), "3d")
        self.assertEqual(M.dimension("a 2 d sprite"), "2d")
        self.assertEqual(M.dimension("a 3d model and a 2d icon of it"), "both")
        self.assertEqual(M.dimension("version 1.2d of the file"), "")
        self.assertEqual(M.dimension("3dsmax import"), "")

    def test_neither_asks_first(self):
        out, dim = M.apply("/gamemodel a treasure chest", B)
        self.assertEqual(dim, "")
        self.assertIn("did not say 2D or 3D", out)

    def test_the_always_on_notes_no_longer_carry_the_tools(self):
        forge = cc_session._forge_note() if hasattr(cc_session, "_forge_note") else ""
        self.assertNotIn("polyhaven", forge.lower())
        self.assertNotIn("parts.<key>.maps", forge)


if __name__ == "__main__":
    unittest.main()

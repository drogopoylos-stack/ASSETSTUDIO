"""An adopted session's signature must equal the one a send builds, or every backend restart costs
the next message a respawn and a 'Setting changed mid-conversation' warning nobody caused."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from asset_studio import cc_session as C  # noqa: E402


class AdoptedSig(unittest.TestCase):
    def test_a_signature_survives_the_registry_file(self):
        sig = ("claude.exe", "D:/game", "default", "bypassPermissions", False, "high", False, "", False,
               (True, True, False), ("studio-sonnet-5-5-v1",))
        stored = json.loads(json.dumps({"sig": list(sig)}))["sig"]
        self.assertNotEqual(tuple(stored), sig)              # the old reading: never equal
        self.assertEqual(C._sig_from_json(stored), sig)      # the fix

    def test_nothing_stored_is_an_empty_signature(self):
        self.assertEqual(C._sig_from_json(None), ())
        self.assertEqual(C._sig_from_json([]), ())


if __name__ == "__main__":
    unittest.main()

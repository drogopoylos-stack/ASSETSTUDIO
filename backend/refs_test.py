"""refs: the human reference library — search by whole words, requests checked, copies into a game."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from asset_studio import refs as R  # noqa: E402


def ref(root: Path, sub: str, slug: str, **meta):
    d = root / sub / slug
    d.mkdir(parents=True)
    for f in ("model.glb", "front.png", "side.png"):
        (d / f).write_bytes(b"x")
    (d / "meta.json").write_text(json.dumps({"id": meta.pop("id"), "files": {"model": "model.glb", "front": "front.png", "side": "side.png"}, **meta}))


class Refs(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self._ref = R.REF
        R.REF = self.root / "references"
        ref(R.REF, "humans", "body_male_realistic", id="hbm/body_male_realistic", name="Body Male - Realistic", kind="body", style="realistic", sex="male", dimensions_m=[0.88, 1.69, 0.29])
        ref(R.REF, "humans", "body_female_realistic", id="hbm/body_female_realistic", name="Body Female - Realistic", kind="body", style="realistic", sex="female", dimensions_m=[0.84, 1.64, 0.28])
        ref(R.REF, "humans", "hand_stylized", id="hbm/hand_stylized", name="Hand - Stylized", kind="hand", style="stylized")

    def tearDown(self):
        R.REF = self._ref
        self.dir.cleanup()

    def test_whole_words_male_is_not_female(self):
        ids = [r["id"] for r in R.search("male body")["rows"]]
        self.assertEqual(ids, ["hbm/body_male_realistic"])

    def test_a_row_carries_absolute_paths_and_its_size(self):
        r = R.search("hand")["rows"][0]
        self.assertTrue(Path(r["model_path"]).is_absolute() and r["model_path"].endswith("model.glb"))
        self.assertTrue(r["front_path"].endswith("front.png"))
        self.assertEqual(R.search("", kind="body")["total"], 2)

    def test_an_empty_library_says_how_to_fill_it(self):
        R.REF = self.root / "nothing"
        self.assertIn("prepare", R.search("")["hint"])

    def test_a_bad_rig_is_refused_before_blender_runs(self):
        self._status = R.status
        R.status = lambda: {"humans": True, "mpfb": True, "blender": True}
        try:
            self.assertIn("rig is one of", R.human({"rig": "nope"})["error"])
        finally:
            R.status = self._status

    def test_get_by_id_and_a_miss(self):
        self.assertTrue(R.get("hbm/hand_stylized")["ok"])
        self.assertIn("no reference", R.get("hbm/nope")["error"])


if __name__ == "__main__":
    unittest.main()

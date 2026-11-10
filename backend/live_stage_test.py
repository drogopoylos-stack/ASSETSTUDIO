"""The 2D scene tools, with the page replaced by a fake: what they send, and what they keep on disk.

The page itself is tested in frontend/tests/stage.test.ts, and with real Phaser, PixiJS and HTML games
through the live routes. What is held here is the part that can damage saved work: the file, undo,
and the choice between the 2D and the 3D tools.
"""
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from asset_studio import live as L  # noqa: E402
from asset_studio import live_scene as S  # noqa: E402
from asset_studio import live_stage as ST  # noqa: E402


class FakePage:
    """Answers `stageCall` the way stage.ts does."""

    def __init__(self, engine="phaser"):
        self.engine = engine
        self.calls = []
        self.use3d = False

    def __call__(self, project, op, args=None):
        self.calls.append((op, args))
        if self.use3d:
            return {"ok": False, "use3d": True}
        if op == "edit":
            results = []
            for sp in args["specs"]:
                r = {"ok": True, "key": "Main/" + sp["target"], "name": sp["target"]}
                save = {}
                if "pos" in sp:
                    save["pos"] = [sp["pos"][0], sp["pos"][1], 0]
                if "rot" in sp:
                    save["rot"] = [0, 0, sp["rot"]]
                if "alpha" in sp:
                    save["alpha"] = sp["alpha"]
                if "visible" in sp:
                    save["hidden"] = not sp["visible"]
                r.update(save=save, undo={"pos": [1, 2]}, before={"pos": [1, 2, 0]}, after=save)
                if sp["target"] == "placedone":
                    r["placed"] = "p1"
                results.append(r)
            return {"ok": True, "engine": self.engine, "results": results}
        if op == "at":
            return {"ok": True, "pos": [50, 60]}
        if op == "list":
            return {"ok": True, "engine": self.engine, "view": [100, 100], "rows": []}
        if op == "place":
            return {"ok": True, "id": args["item"]["id"], "key": "placed/" + args["item"]["id"], "name": args["item"]["name"]}
        return {"ok": True, "restored": [], "removed": []}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.game = self.dir.name
        self.page = FakePage()
        self._call = ST._call
        self._entry = L._entry
        self._enabled = L.enabled
        self._guard = L._guard
        self._scene_off = S._off
        # The person's own "Scene edit" switch is not what these tests are about.
        S._off = lambda: None
        L._guard = lambda p: None
        ST._call = self.page
        L.enabled = lambda: True
        L._entry = lambda p: {"url": "http://x/", "engine": "phaser", "project": p}
        S._UNDO.clear()
        S._BAKED.clear()

    def tearDown(self):
        ST._call = self._call
        L._entry = self._entry
        L.enabled = self._enabled
        L._guard = self._guard
        S._off = self._scene_off
        self.dir.cleanup()

    def doc(self):
        return json.loads((Path(self.game) / S.EDITS_NAME).read_text(encoding="utf-8"))


class Requests(unittest.TestCase):
    def specs(self, **body):
        return ST._specs(body)

    def test_a_turn_is_degrees_for_an_agent_and_radians_for_the_page(self):
        sp, err, _ = self.specs(target="a", rot=[0, 0, 90])
        self.assertEqual(err, "")
        self.assertAlmostEqual(sp[0]["rot"], math.pi / 2)
        sp, _, _ = self.specs(target="a", rotate=45)
        self.assertAlmostEqual(sp[0]["rotate"], math.pi / 4)

    def test_a_3d_shaped_position_is_taken_as_written(self):
        sp, err, _ = self.specs(target="a", pos=[1, 2, 3])
        self.assertEqual(err, "")
        self.assertEqual(sp[0]["pos"], [1.0, 2.0])

    def test_bad_values_are_refused_with_the_field(self):
        for body, word in (({"target": "a", "pos": [1]}, "pos"), ({"target": "a", "move": "x"}, "move"),
                           ({"target": "a", "rot": "x"}, "rot"), ({"target": "a", "scale": []}, "scale"),
                           ({"target": "a", "alpha": "x"}, "alpha")):
            _, err, _ = ST._specs(body)
            self.assertIn(word, err, body)

    def test_no_target_and_no_change_are_refused(self):
        self.assertIn("nothing to do", self.specs()[1])
        self.assertIn("no target", self.specs(pos=[1, 2])[1])
        self.assertIn("changes nothing", self.specs(target="a")[1])

    def test_several_edits_at_once(self):
        sp, err, _ = ST._specs({"edits": [{"target": "a", "alpha": 0.5}, {"name": "b", "visible": False}]})
        self.assertEqual(err, "")
        self.assertEqual([s["target"] for s in sp], ["a", "b"])

    def test_a_public_folder_is_the_root_the_game_is_served_from(self):
        self.assertEqual(ST.served_path("x", "public/art/a.png"), "art/a.png")
        self.assertEqual(ST.served_path("x", "game/public/a.png"), "a.png")
        self.assertEqual(ST.served_path("x", "src\\art\\a.png"), "src/art/a.png")
        self.assertEqual(ST.served_path("x", "art/a.png"), "art/a.png")


class Routing(Base):
    def test_each_engine_goes_to_the_right_tools(self):
        for engine, want in (("phaser", True), ("pixi", True), ("dom", True), ("canvas2d", True), ("webgl", True),
                             ("three", False), ("playcanvas", False), ("babylon", False), ("", False)):
            L._entry = lambda p, e=engine: {"url": "http://x/", "engine": e}
            self.assertEqual(ST.handles(self.game), want, engine)

    def test_a_page_that_turned_out_to_be_3d_falls_through(self):
        self.page.use3d = True
        self.assertIsNone(ST.edit(self.game, {"target": "a", "pos": [1, 2]}))
        Path(self.game, "a.png").write_bytes(b"x")
        self.assertIsNone(ST.place(self.game, {"asset": "a.png"}))


class Saving(Base):
    def test_a_try_writes_nothing(self):
        r = ST.edit(self.game, {"target": "hero", "pos": [5, 6]})
        self.assertTrue(r["ok"])
        self.assertFalse(r["saved"])
        self.assertFalse((Path(self.game) / S.EDITS_NAME).exists())

    def test_a_save_writes_the_document_a_3d_game_uses(self):
        r = ST.edit(self.game, {"target": "hero", "pos": [5, 6], "rot": [0, 0, 90], "alpha": 0.5, "save": True})
        self.assertTrue(r["saved"])
        part = self.doc()["parts"]["Main/hero"]
        self.assertEqual(part["pos"], [5.0, 6.0, 0.0])
        self.assertAlmostEqual(part["rot"][2], math.pi / 2)
        self.assertEqual(part["alpha"], 0.5)
        self.assertEqual(r["written"]["rot"], 90.0)        # degrees back to the agent

    def test_only_an_html_game_names_the_document_as_its_target(self):
        ST.edit(self.game, {"target": "a", "pos": [1, 2], "save": True})
        self.assertNotIn("stage", self.doc())
        self.page.engine = "dom"
        ST.edit(self.game, {"target": "a", "pos": [1, 2], "save": True})
        self.assertEqual(self.doc()["stage"], "dom")

    def test_what_was_there_stays(self):
        S.write_doc(self.game, {"version": 1, "parts": {"other": {"hidden": True}}, "note": "mine"})
        ST.edit(self.game, {"target": "hero", "alpha": 0.5, "save": True})
        d = self.doc()
        self.assertEqual(d["parts"]["other"], {"hidden": True})
        self.assertEqual(d["note"], "mine")

    def test_a_broken_file_is_never_written_over(self):
        p = Path(self.game) / S.EDITS_NAME
        p.write_text("{broken", encoding="utf-8")
        r = ST.edit(self.game, {"target": "hero", "alpha": 0.5, "save": True})
        self.assertFalse(r["ok"])
        self.assertEqual(p.read_text(encoding="utf-8"), "{broken")

    def test_undo_takes_it_back_live_and_on_disk(self):
        ST.edit(self.game, {"target": "hero", "pos": [5, 6], "save": True})
        ST.edit(self.game, {"target": "hero", "pos": [7, 8], "save": True})
        r = S.edit(self.game, {"undo": True})
        self.assertTrue(r["ok"])
        self.assertEqual(self.doc()["parts"]["Main/hero"]["pos"], [5.0, 6.0, 0.0])
        self.assertIn(("restore", {"items": [{"key": "Main/hero", "vals": {"pos": [1, 2]}}]}), self.page.calls)
        S.edit(self.game, {"undo": True})
        self.assertFalse((Path(self.game) / S.EDITS_NAME).exists(), "the undo of the call that made the file removes it")

    def test_a_file_with_only_the_html_mark_counts_as_empty(self):
        self.assertTrue(S.is_empty_doc({"version": 1, "stage": "dom", "parts": {}}))

    def test_a_clear_goes_through_the_file(self):
        ST.edit(self.game, {"target": "hero", "alpha": 0.5, "save": True})
        r = S.edit(self.game, {"clear": ["Main/hero"]})
        self.assertEqual(r["cleared"], ["Main/hero"])
        self.assertFalse((Path(self.game) / S.EDITS_NAME).exists())


class TheAgentsSwitch(Base):
    """`cc_scene_edit` off stops an agent. It does not stop a person in the Edit tab."""

    def setUp(self):
        super().setUp()
        self._off = S._off
        S._off = lambda: {"ok": False, "error": "Scene edit is off."}

    def tearDown(self):
        S._off = self._off
        super().tearDown()

    def test_an_agent_is_refused(self):
        self.assertIn("off", S.edit(self.game, {"target": "hero", "alpha": 0.5})["error"])
        self.assertIn("off", S.place(self.game, {"asset": "a.png"})["error"])
        self.assertIn("off", S.objects(self.game)["error"])

    def test_the_edit_tab_is_not(self):
        self.assertTrue(S.edit(self.game, {"target": "hero", "alpha": 0.5, "from_ui": True})["ok"])
        self.assertTrue(ST.view(self.game).get("ok"))
        self.assertTrue(ST.pause(self.game, True).get("ok"))
        Path(self.game, "star.png").write_bytes(b"x")
        self.assertTrue(S.place(self.game, {"asset": "star.png", "from_ui": True})["ok"])


class Placing(Base):
    def test_it_places_an_image_and_keeps_it(self):
        Path(self.game, "public").mkdir()
        Path(self.game, "public", "star.png").write_bytes(b"x")
        r = ST.place(self.game, {"asset": "public/star.png", "pos": [10, 20], "scale": 2, "save": True})
        self.assertTrue(r["saved"])
        it = self.doc()["placed"][0]
        self.assertEqual(it["ref"], {"kind": "image", "url": "star.png"})
        self.assertEqual(it["pos"], [10.0, 20.0, 0.0])
        self.assertEqual(it["scale"], [2.0, 2.0, 1.0])

    def test_with_no_place_named_it_goes_to_the_middle_of_the_view(self):
        Path(self.game, "star.png").write_bytes(b"x")
        r = ST.place(self.game, {"asset": "star.png"})
        self.assertEqual(r["pos"], [50, 60])
        self.assertFalse(r["saved"])

    def test_only_an_image_of_this_project(self):
        self.assertIn("images only", ST.place(self.game, {"asset": "a.glb"})["error"])
        self.assertIn("no such image", ST.place(self.game, {"asset": "gone.png"})["error"])
        self.assertIn("path of an image", ST.place(self.game, {})["error"])

    def test_undo_takes_a_placement_out(self):
        Path(self.game, "star.png").write_bytes(b"x")
        r = ST.place(self.game, {"asset": "star.png", "save": True})
        S.edit(self.game, {"undo": True})
        self.assertIn(("unplace", {"ids": [r["id"]]}), self.page.calls)
        self.assertFalse((Path(self.game) / S.EDITS_NAME).exists())

    def test_a_placed_image_moved_stays_in_its_entry(self):
        Path(self.game, "star.png").write_bytes(b"x")
        ST.place(self.game, {"asset": "star.png", "id": "p1", "save": True})
        ST.edit(self.game, {"target": "placedone", "pos": [7, 8], "save": True})
        d = self.doc()
        self.assertEqual(d["placed"][0]["pos"], [7.0, 8.0, 0.0])
        self.assertEqual(d["parts"], {})


if __name__ == "__main__":
    unittest.main()

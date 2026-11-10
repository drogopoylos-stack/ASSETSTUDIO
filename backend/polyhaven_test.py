"""Poly Haven downloads, with the network replaced by a fake: what is fetched, where it lands, what is
refused, and the license kept beside it. The real service was driven by hand on 2026-11-09 (a model,
a texture set and an HDRI); this holds the rules between those runs, offline."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from asset_studio import polyhaven as P  # noqa: E402
from asset_studio import workspace as W  # noqa: E402

GLTF = b'{"asset":{"version":"2.0"},"buffers":[{"uri":"axe.bin","byteLength":64}],"bufferViews":[{"buffer":0,"byteLength":64}],"images":[{"uri":"textures/axe_diff_1k.jpg"}]}'
BIN = b"\x00" * 64
DIFF = b"jpeg-bytes"
md5 = lambda b: hashlib.md5(b).hexdigest()  # noqa: E731

INFO = {"axe": {"name": "Axe", "type": 2, "categories": ["tools"], "tags": ["axe"], "authors": {"Ann": "All"},
                "dimensions": [50, 200, 700], "polycount": 3000, "download_count": 9},
        "rock": {"name": "Rock Wall", "type": 1, "categories": ["rock"], "tags": ["wall"], "authors": {"Bo": "All"}, "download_count": 3}}
FILES = {
    "axe": {"gltf": {"1k": {"gltf": {"url": "https://dl.polyhaven.org/m/axe_1k.gltf", "size": len(GLTF), "md5": md5(GLTF),
                                     "include": {"axe.bin": {"url": "https://dl.polyhaven.org/m/axe.bin", "size": len(BIN), "md5": md5(BIN)},
                                                 "textures/axe_diff_1k.jpg": {"url": "https://dl.polyhaven.org/m/d.jpg", "size": len(DIFF), "md5": md5(DIFF)}}}}}},
    "rock": {"Diffuse": {"1k": {"jpg": {"url": "https://dl.polyhaven.org/t/rock_diff_1k.jpg", "size": len(DIFF), "md5": md5(DIFF)}}},
             "nor_gl": {"1k": {"jpg": {"url": "https://dl.polyhaven.org/t/rock_nor_gl_1k.jpg", "size": len(DIFF), "md5": md5(DIFF)}}}},
}
BLOBS = {"https://dl.polyhaven.org/m/axe_1k.gltf": GLTF, "https://dl.polyhaven.org/m/axe.bin": BIN,
         "https://dl.polyhaven.org/m/d.jpg": DIFF, "https://dl.polyhaven.org/t/rock_diff_1k.jpg": DIFF,
         "https://dl.polyhaven.org/t/rock_nor_gl_1k.jpg": DIFF}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.calls = []
        self._get, self._check, self._lic = P._get, W._check_browse, P._license_text
        P._get = self.fake
        W._check_browse = lambda p: Path(p).resolve()
        P._license_text = lambda: "All assets are licensed as CC0."

    def tearDown(self):
        P._get, W._check_browse, P._license_text = self._get, self._check, self._lic
        self.dir.cleanup()

    def fake(self, url, timeout=60.0):
        self.calls.append(url)
        if url.startswith(P.API + "/info/"):
            return json.dumps(INFO[url.rsplit("/", 1)[1]]).encode()
        if url.startswith(P.API + "/files/"):
            return json.dumps(FILES[url.rsplit("/", 1)[1]]).encode()
        if "cdn.polyhaven.com" in url:
            return b"png"
        return BLOBS[url]


class Download(Base):
    def test_a_model_lands_with_its_files_renders_and_license(self):
        game = self.root / "game"
        (game / "public").mkdir(parents=True)
        r = P.download(str(game), "axe", "models", "1k")
        self.assertTrue(r["ok"], r)
        folder = game / "public/assets/polyhaven/models/axe"
        for f in ("axe_1k.gltf", "axe_1k.glb", "axe.bin", "textures/axe_diff_1k.jpg", "reference.png", "reference-clay.png", "LICENSE.txt", "polyhaven.json"):
            self.assertTrue((folder / f).is_file(), f)
        self.assertEqual(r["main"], "public/assets/polyhaven/models/axe/axe_1k.glb")
        self.assertTrue(Path(r["main_path"]).is_file() and Path(r["references"]["reference_clay"]).is_file())
        self.assertEqual(r["served_as"], "/assets/polyhaven/models/axe/axe_1k.glb")
        glb = (folder / "axe_1k.glb").read_bytes()
        self.assertEqual(glb[:4], b"glTF")
        self.assertIn(DIFF, glb)
        self.assertIn(BIN, glb)
        self.assertEqual(r["dimensions_m"], [0.05, 0.2, 0.7])
        lic = (folder / "LICENSE.txt").read_text(encoding="utf-8")
        self.assertIn("CC0", lic)
        self.assertIn("https://polyhaven.com/a/axe", lic)
        self.assertIn("Ann", lic)

    def test_a_project_without_public_keeps_assets_at_its_root(self):
        r = P.download(str(self.root), "axe", "models", "1k")
        self.assertTrue(r["main"].startswith("assets/polyhaven/models/axe/"), r["main"])
        self.assertNotIn("served_as", r)

    def test_a_second_download_fetches_nothing_it_has(self):
        P.download(str(self.root), "axe", "models", "1k")
        self.calls.clear()
        r = P.download(str(self.root), "axe", "models", "1k")
        self.assertEqual((r["fetched"], r["kept"]), (0, 3))
        self.assertFalse([c for c in self.calls if "dl.polyhaven.org" in c])

    def test_a_damaged_file_is_not_kept(self):
        BLOBS["https://dl.polyhaven.org/m/axe.bin"] = b"wrong"
        try:
            r = P.download(str(self.root), "axe", "models", "1k")
        finally:
            BLOBS["https://dl.polyhaven.org/m/axe.bin"] = BIN
        self.assertFalse(r["ok"])
        self.assertIn("md5", r["error"])
        self.assertFalse((self.root / "assets/polyhaven/models/axe/axe.bin").exists())

    def test_a_texture_set_names_its_maps(self):
        r = P.download(str(self.root), "rock", "textures", "1k")
        self.assertEqual(sorted(r["maps"]), ["baseColor", "normal"])
        mat = json.loads((self.root / r["material"]).read_text(encoding="utf-8"))
        self.assertEqual(mat["maps"]["normal"], "rock_nor_gl_1k.jpg")

    def test_an_id_keeps_its_case(self):
        self.assertEqual(P._check_id("Barrel_01"), "Barrel_01")

    def test_the_wrong_type_and_bad_input_are_refused(self):
        self.assertIn("not one of the hdris", P.download(str(self.root), "axe", "hdris", "1k")["error"])
        self.assertIn("asset id", P.download(str(self.root), "../axe", "models", "1k")["error"])
        self.assertIn("res is one of", P.download(str(self.root), "axe", "models", "16k")["error"])

    def test_a_listed_path_that_leaves_the_folder_is_refused(self):
        inc = FILES["axe"]["gltf"]["1k"]["gltf"]["include"]
        inc["../../evil.js"] = {"url": "https://dl.polyhaven.org/m/axe.bin", "size": 1, "md5": md5(BIN)}
        try:
            r = P.download(str(self.root), "axe", "models", "1k")
        finally:
            del inc["../../evil.js"]
        self.assertFalse(r["ok"])
        self.assertFalse((self.root / "evil.js").exists())

    def test_only_poly_haven_hosts_are_fetched(self):
        P._get = self._get
        with self.assertRaises(ValueError):
            P._get("https://example.com/a.gltf")

    def test_downloaded_lists_what_the_project_has(self):
        P.download(str(self.root), "axe", "models", "1k")
        P.download(str(self.root), "rock", "textures", "1k")
        rows = P.downloaded(str(self.root))["rows"]
        self.assertEqual(sorted((r["type"], r["id"]) for r in rows), [("models", "axe"), ("textures", "rock")])
        rock = next(r for r in rows if r["id"] == "rock")
        self.assertEqual(rock["maps"]["normal"], "assets/polyhaven/textures/rock/rock_nor_gl_1k.jpg")
        inner = self.root / "src" / "deep"
        inner.mkdir(parents=True, exist_ok=True)
        r = P.downloaded(str(inner))
        self.assertEqual(Path(r["root"]).resolve(), self.root.resolve())
        self.assertEqual(len(r["rows"]), 2)


class Search(Base):
    def setUp(self):
        super().setUp()
        self._index = P.index
        P.index = lambda kind, fresh=False: {k: v for k, v in INFO.items() if v["type"] == P.TYPES[kind]}

    def tearDown(self):
        P.index = self._index
        super().tearDown()

    def test_every_word_must_match(self):
        self.assertEqual([r["id"] for r in P.search("models", "axe")["rows"]], ["axe"])
        self.assertEqual(P.search("models", "axe rock")["total"], 0)

    def test_rows_carry_the_real_size_in_metres(self):
        row = P.search("models", "")["rows"][0]
        self.assertEqual(row["dimensions_m"], [0.05, 0.2, 0.7])
        self.assertEqual(row["license"], "CC0")


if __name__ == "__main__":
    unittest.main()

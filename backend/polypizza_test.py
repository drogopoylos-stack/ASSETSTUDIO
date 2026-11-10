"""Poly Pizza, offline: rows, hosts, ids and the missing-key answer. No request leaves the machine."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from asset_studio import polypizza as P  # noqa: E402


class PolyPizza(unittest.TestCase):
    def setUp(self):
        self._key = P._key

    def tearDown(self):
        P._key = self._key

    def test_a_row_says_licence_credit_and_animation(self):
        r = P._row({"ID": "abc123", "Title": "Wolf", "Licence": "CC0 1.0", "Creator": {"Username": "Quaternius"},
                    "Animated": True, "Tri Count": 1928, "Download": "https://static.poly.pizza/x.glb"})
        self.assertTrue(r["cc0"] and r["animated"])
        self.assertEqual((r["creator"], r["triangles"]), ("Quaternius", 1928))
        self.assertFalse(P._row({"Licence": "CC-BY 3.0"})["cc0"])

    def test_only_poly_pizza_hosts_are_fetched(self):
        with self.assertRaises(ValueError):
            P._get("https://example.com/model.glb", auth=False)

    def test_no_key_is_said_plainly(self):
        P._key = lambda: ""
        self.assertIn("no Poly Pizza key", P.search("wolf")["error"])
        self.assertFalse(P.status()["key"])

    def test_a_bad_id_is_refused_before_any_request(self):
        P._key = lambda: "k"
        for bad in ("../x", "", "a b", "x" * 40):
            self.assertIn("not a Poly Pizza model id", P.download("D:/nowhere", bad)["error"])

    def test_an_empty_search_is_refused(self):
        P._key = lambda: "k"
        self.assertIn("q is required", P.search("  ")["error"])


if __name__ == "__main__":
    unittest.main()

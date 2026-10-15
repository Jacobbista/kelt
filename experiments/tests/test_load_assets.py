import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "exposure"))
import load_assets  # noqa: E402

STORE = {"version": 4, "assets": [
    {"assetId": "demo-asset-01", "org": "demo", "capabilities": [{"source": "synthetic", "positioningId": "demo-001"}]}]}


class LoadAssetsTest(unittest.TestCase):
    def test_ids(self):
        self.assertEqual(load_assets.ids("kelt-load-", 3), ["kelt-load-01", "kelt-load-02", "kelt-load-03"])

    def test_add_keeps_the_store_and_maps_each_id_to_itself(self):
        out = load_assets.add(STORE, "kelt-load-", 50, "demo")
        self.assertEqual(out["version"], 4)
        self.assertEqual(len(out["assets"]), 51)
        self.assertEqual(out["assets"][0], STORE["assets"][0])
        a = out["assets"][-1]
        self.assertEqual((a["assetId"], a["org"], a["capabilities"]),
                         ("kelt-load-50", "demo", [{"source": "synthetic", "positioningId": "kelt-load-50"}]))

    def test_add_twice_does_not_duplicate_and_remove_restores(self):
        twice = load_assets.add(load_assets.add(STORE, "kelt-load-", 5, "demo"), "kelt-load-", 5, "demo")
        self.assertEqual(len(twice["assets"]), 6)
        self.assertEqual(load_assets.remove(twice, "kelt-load-"), STORE)

    def test_points_one_per_asset_inside_the_room(self):
        pts = load_assets.points(50, 32.0, 13.48)
        self.assertEqual(len(pts), 50)
        self.assertEqual(len(set(pts)), 50)
        self.assertTrue(all(0 < x < 32.0 and 0 < y < 13.48 for x, y in pts))

    def test_bodies_ask_maxage_zero(self):
        self.assertEqual(json.loads(load_assets.bodies("kelt-load-", 2)[1]),
                         {"device": {"assetId": "kelt-load-02"}, "maxAge": 0})


if __name__ == "__main__":
    unittest.main()

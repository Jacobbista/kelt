import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "exposure"))
import examples  # noqa: E402


class ExamplesTest(unittest.TestCase):
    def test_partial_circle_1_5_m_east(self):
        lat, lon = 59.4, 17.9
        cs = {c[0]: c for c in examples.cases("a", lat, lon)}
        area = cs["verify_partial"][2]["area"]
        dx = (area["center"]["longitude"] - lon) * 111320.0 * math.cos(math.radians(lat))
        self.assertAlmostEqual(dx, 1.5, places=6)
        self.assertEqual((area["radius"], cs["verify_inside"][2]["area"]["radius"]), (2, 50))
        self.assertEqual(cs["other_org"][3], "other organisation")
        self.assertEqual(cs["public_phone"][2], {"device": {"phoneNumber": "+46700000000"}})


if __name__ == "__main__":
    unittest.main()

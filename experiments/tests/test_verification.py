import os
import sys
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "exposure"))
import verification  # noqa: E402


def contract():
    with open(os.path.join(HERE, "fixtures", "contract_retrieval.yaml")) as fh:
        return yaml.safe_load(fh)


class ExpectErrorTest(unittest.TestCase):
    def test_declared_pairs(self):
        c = contract()
        self.assertTrue(verification.expect_error(c, 422, "UNSUPPORTED_IDENTIFIER"))
        self.assertTrue(verification.expect_error(c, 401, "UNAUTHENTICATED"))
        self.assertTrue(verification.expect_error(c, 404, "IDENTIFIER_NOT_FOUND"))

    def test_undeclared_pair(self):
        self.assertFalse(verification.expect_error(contract(), 400, "UNSUPPORTED_IDENTIFIER"))


class DataTest(unittest.TestCase):
    # The live sample of 2026-09-26: API answer and adapter /measurement for the Wittra tag.
    API = {"lastLocationTime": "2026-09-15T15:09:28Z",
           "area": {"areaType": "CIRCLE",
                    "center": {"latitude": 59.404819831058994, "longitude": 17.949413231268892}, "radius": 1.0},
           "source": "wittra", "kind": "uwb-tag", "horizontalAccuracy": 1.0, "altitude": 31.0}
    ADAPTER = {"source": "wittra", "frame": "wgs84", "latitude": 59.404819831058994,
               "longitude": 17.949413231268892, "timestamp": 1789484968.009564, "y": 0.0}

    def rows(self, api=None, adapter=None):
        return {r["case"]: r for r in verification.check_data(api or self.API, adapter or self.ADAPTER, 1.0, "uwb-tag")}

    def test_live_sample_passes(self):
        r = self.rows()
        for case in ("coordinates", "timestamp", "source", "kind", "accuracy", "radius_floor"):
            self.assertEqual(r[case]["verdict"], "pass", case)

    def test_altitude_is_observed_only(self):
        self.assertEqual(self.rows()["altitude"]["verdict"], "observed")

    def test_moved_coordinates_fail(self):
        api = dict(self.API, area=dict(self.API["area"], center={"latitude": 59.5, "longitude": 17.9}))
        self.assertEqual(self.rows(api=api)["coordinates"]["verdict"], "fail")

    def test_radius_below_one_fails(self):
        api = dict(self.API, area=dict(self.API["area"], radius=0.4))
        self.assertEqual(self.rows(api=api)["radius_floor"]["verdict"], "fail")

    def test_adapter_accuracy_wins_over_class(self):
        adapter = dict(self.ADAPTER, horizontalAccuracy=0.3)
        api = dict(self.API, horizontalAccuracy=0.3)
        r = self.rows(api=api, adapter=adapter)
        self.assertEqual(r["accuracy"]["verdict"], "pass")
        self.assertEqual(r["radius_floor"]["verdict"], "pass")


class RunCaseTest(unittest.TestCase):
    def test_unreachable_is_recorded_not_raised(self):
        r = verification.run_case("x", "POST", "http://127.0.0.1:9/nothing", {}, {}, timeout=1)
        self.assertEqual(r["status"], 0)
        self.assertTrue(r["error"])

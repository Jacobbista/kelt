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
    # The live sample of 2026-10-08 on northbound 0.19.1: API answer and adapter
    # /measurement for the Wittra tag (z: false, no per-fix accuracy, lastSeen null).
    API = {"lastLocationTime": "2026-09-15T15:09:28Z",
           "area": {"areaType": "CIRCLE",
                    "center": {"latitude": 59.404819831058994, "longitude": 17.949413231268892}, "radius": 1.0},
           "source": "wittra", "kind": "uwb-tag", "horizontalAccuracy": 1.0}
    ADAPTER = {"source": "wittra", "frame": "wgs84", "latitude": 59.404819831058994,
               "longitude": 17.949413231268892, "timestamp": 1789484968.009564, "lastSeen": None}

    def rows(self, api=None, adapter=None, **kw):
        return {r["case"]: r for r in verification.check_data(api or self.API, adapter or self.ADAPTER, 1.0, "uwb-tag", **kw)}

    def test_live_sample_passes(self):
        r = self.rows()
        for case in ("coordinates", "timestamp", "source", "kind", "accuracy", "radius_floor", "altitude"):
            self.assertEqual(r[case]["verdict"], "pass", case)

    def test_altitude_is_georef_plus_z(self):
        adapter = dict(self.ADAPTER, z=1.5)
        self.assertEqual(self.rows(api=dict(self.API, altitude=32.5), adapter=adapter, georef_altitude=31.0)["altitude"]["verdict"], "pass")
        self.assertEqual(self.rows(api=dict(self.API, altitude=1.5), adapter=adapter, georef_altitude=31.0)["altitude"]["verdict"], "fail")

    def test_altitude_absent_without_a_surveyed_origin(self):
        adapter = dict(self.ADAPTER, z=1.5)
        self.assertEqual(self.rows(adapter=adapter, georef_altitude=None)["altitude"]["verdict"], "pass")
        self.assertEqual(self.rows(api=dict(self.API, altitude=1.5), adapter=adapter)["altitude"]["verdict"], "fail")

    def test_established_time_on_motion_is_the_later_of_fix_and_last_seen(self):
        adapter = dict(self.ADAPTER, lastSeen=1789484968.009564 + 3600)
        later = dict(self.API, lastLocationTime="2026-09-15T16:09:28Z")
        self.assertEqual(self.rows(api=later, adapter=adapter, reporting="on_motion")["timestamp"]["verdict"], "pass")
        self.assertEqual(self.rows(api=later, adapter=adapter, reporting=None)["timestamp"]["verdict"], "fail")

    def test_moved_coordinates_fail(self):
        api = dict(self.API, area=dict(self.API["area"], center={"latitude": 59.5, "longitude": 17.9}))
        self.assertEqual(self.rows(api=api)["coordinates"]["verdict"], "fail")

    def test_radius_below_one_fails(self):
        api = dict(self.API, area=dict(self.API["area"], radius=0.4))
        self.assertEqual(self.rows(api=api)["radius_floor"]["verdict"], "fail")

    def test_adapter_accuracy_wins_over_the_assumed_one(self):
        adapter = dict(self.ADAPTER, accuracy=0.3)
        api = dict(self.API, horizontalAccuracy=0.3)
        r = self.rows(api=api, adapter=adapter)
        self.assertEqual(r["accuracy"]["verdict"], "pass")
        self.assertEqual(r["radius_floor"]["verdict"], "pass")


class MaxAgeZeroTest(unittest.TestCase):
    # northbound 0.19: maxAge=0 is served only when the position is current.
    def test_on_request_is_always_current(self):
        self.assertEqual(verification.maxage_zero_expected("on_request", None, 0, 10_000), 200)

    def test_periodic_and_on_motion_within_the_interval(self):
        self.assertEqual(verification.maxage_zero_expected("periodic", 2, 100, 101.5), 200)
        self.assertEqual(verification.maxage_zero_expected("on_motion", 60, 100, 200), 422)

    def test_undeclared_is_never_current(self):
        self.assertEqual(verification.maxage_zero_expected(None, None, 100, 100), 422)


class RunCaseTest(unittest.TestCase):
    def test_unreachable_is_recorded_not_raised(self):
        r = verification.run_case("x", "POST", "http://127.0.0.1:9/nothing", {}, {}, timeout=1)
        self.assertEqual(r["status"], 0)
        self.assertTrue(r["error"])


class OutageTest(unittest.TestCase):
    # A vendor outage shows as a declared 422 or 5xx; a 401 (an expired token
    # during a long fault group) is not one.
    def test_declared_outage_codes(self):
        c = contract()
        self.assertTrue(verification.is_outage({"status": 422, "code": "UNABLE_TO_LOCATE"}, c)
                        or not verification.expect_error(c, 422, "UNABLE_TO_LOCATE"))
        self.assertFalse(verification.is_outage({"status": 401, "code": "UNAUTHENTICATED"}, c))
        self.assertFalse(verification.is_outage({"status": 200, "code": None}, c))

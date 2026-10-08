import json
import unittest

from app.models import PodSummary
from app.services.northbound_service import contract_pod, migrate_capabilities, running_pods, schema_hints


class MigrateCapabilitiesTest(unittest.TestCase):
    def test_vendor_local_becomes_venue(self):
        out = migrate_capabilities('{"source":"acme","frame":"local"}', "vendor-adapter")
        self.assertEqual(json.loads(out)["frame"], "venue")

    def test_wifi_local_is_dropped_for_the_image_frame(self):
        out = migrate_capabilities('{"source":"wifi","frame":"local","z":false}', "wifi-adapter")
        self.assertNotIn("frame", json.loads(out))
        self.assertEqual(json.loads(out)["source"], "wifi")

    def test_current_frames_are_left_alone(self):
        for frame in ("room", "venue", "wgs84"):
            self.assertIsNone(migrate_capabilities(json.dumps({"frame": frame}), "vendor-adapter"))

    def test_no_frame_no_change(self):
        self.assertIsNone(migrate_capabilities('{"source":"wittra"}', "vendor-adapter"))

    def test_not_an_object_is_not_touched(self):
        for raw in ("", "not json", "[1,2]"):
            self.assertIsNone(migrate_capabilities(raw, "vendor-adapter"))



def _pod(name, start, phase="Running", ready=True, waiting=None, deployment="wittra"):
    return PodSummary(name=name, namespace="positioning", phase=phase, restarts=0, start_time=start,
                      deployment=deployment, ready=ready, waiting_reason=waiting)


class ContractPodTest(unittest.TestCase):
    # During an upgrade the Service reaches only the old ready pod; the contract
    # and the mapping grammar must come from the new one, ready or not.
    def test_newest_running_pod_even_when_not_ready(self):
        pods = [_pod("wittra-old", "2026-09-26T10:00:00+00:00"),
                _pod("wittra-new", "2026-10-08T12:00:00+00:00", ready=False)]
        self.assertEqual(contract_pod(pods, "wittra").name, "wittra-new")

    def test_waiting_or_terminating_pods_are_skipped(self):
        pods = [_pod("wittra-old", "2026-09-26T10:00:00+00:00"),
                _pod("wittra-crash", "2026-10-08T12:00:00+00:00", ready=False, waiting="CrashLoopBackOff"),
                _pod("wittra-gone", "2026-10-08T12:05:00+00:00", phase="Terminating")]
        self.assertEqual(contract_pod(pods, "wittra").name, "wittra-old")

    def test_other_deployments_and_no_pod(self):
        pods = [_pod("wifi-1", "2026-10-08T12:00:00+00:00", deployment="wifi-adapter")]
        self.assertIsNone(contract_pod(pods, "wittra"))


    def test_running_pods_newest_first(self):
        # discover falls back to an older pod: the vendor's raw records do not depend
        # on the adapter release, and a new pod without a loaded schema cannot fetch them.
        pods = [_pod("wittra-old", "2026-09-26T10:00:00+00:00"),
                _pod("wittra-new", "2026-10-08T12:00:00+00:00", ready=False),
                _pod("wittra-crash", "2026-10-08T12:01:00+00:00", waiting="CrashLoopBackOff")]
        self.assertEqual([p.name for p in running_pods(pods, "wittra")], ["wittra-new", "wittra-old"])


class SchemaHintsTest(unittest.TestCase):
    # 0.19: height is `z`; `y` is the horizontal venue coordinate.
    def test_height_is_z(self):
        self.assertTrue(schema_hints({"vendor": "acme", "mapping": {"frame": {"const": "wgs84"}, "z": {"path": "h"}}})["z"])

    def test_venue_y_is_not_height(self):
        h = schema_hints({"mapping": {"frame": {"const": "venue"}, "x": {"path": "a"}, "y": {"path": "b"}}})
        self.assertFalse(h["z"])
        self.assertEqual(h["frame"], "venue")


if __name__ == "__main__":
    unittest.main()

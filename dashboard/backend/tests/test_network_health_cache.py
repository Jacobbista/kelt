import unittest
from unittest import mock

from app.services import network_health_service as nh


def result(interface, status):
    return lambda self, ips: {"interface": interface, "status": status}


class CacheTest(unittest.TestCase):
    def setUp(self):
        nh.NetworkHealthService._last = []
        nh.NetworkHealthService._last_at = 0.0

    def test_results_survive_the_instance(self):
        # The router builds a service per request: the last run must outlive it.
        with mock.patch.object(nh, "_read_ips", return_value={}), \
             mock.patch.object(nh.NetworkHealthService, "_check_n2", result("N2", "ok")), \
             mock.patch.object(nh.NetworkHealthService, "_check_n3", result("N3", "fail")), \
             mock.patch.object(nh.NetworkHealthService, "_check_n4", result("N4", "ok")), \
             mock.patch.object(nh.NetworkHealthService, "_check_n6", result("N6", "ok")):
            nh.NetworkHealthService(k8s=None).run_health_checks()
        cached = nh.NetworkHealthService(k8s=None).get_cached()
        self.assertEqual([(c["interface"], c["status"]) for c in cached],
                         [("N2", "ok"), ("N3", "fail"), ("N4", "ok"), ("N6", "ok")])

    def test_nothing_before_the_first_run(self):
        self.assertEqual(nh.NetworkHealthService(k8s=None).get_cached(), [])


class AgeTest(unittest.TestCase):
    def setUp(self):
        nh.NetworkHealthService._last = []
        nh.NetworkHealthService._last_at = 0.0
        self.runs = 0
        self.now = 1000.0

    def run_checks(self, svc):
        self.runs += 1
        nh.NetworkHealthService._last = [{"interface": "N2", "status": "ok"}]
        nh.NetworkHealthService._last_at = self.now
        return list(nh.NetworkHealthService._last)

    def latest(self, max_age):
        svc = nh.NetworkHealthService(k8s=None)
        with mock.patch.object(nh.NetworkHealthService, "run_health_checks", lambda s: self.run_checks(s)), \
             mock.patch.object(nh.time, "monotonic", lambda: self.now):
            return svc.latest(max_age)

    def test_recent_results_are_reused(self):
        self.latest(25)
        self.now += 10
        self.latest(25)
        self.assertEqual(self.runs, 1)

    def test_old_results_are_rerun(self):
        self.latest(25)
        self.now += 30
        self.latest(25)
        self.assertEqual(self.runs, 2)

    def test_cached_ignores_results_older_than_max_age(self):
        self.latest(25)
        svc = nh.NetworkHealthService(k8s=None)
        with mock.patch.object(nh.time, "monotonic", lambda: self.now + 601):
            self.assertEqual(svc.get_cached(max_age=600), [])
        with mock.patch.object(nh.time, "monotonic", lambda: self.now + 599):
            self.assertEqual(len(svc.get_cached(max_age=600)), 1)


class ConcurrencyTest(unittest.TestCase):
    def setUp(self):
        nh.NetworkHealthService._last = []
        nh.NetworkHealthService._last_at = 0.0

    def test_concurrent_stale_requests_share_one_run(self):
        import threading
        import time as _time
        runs = []

        def slow_run(svc):
            runs.append(1)
            _time.sleep(0.2)
            nh.NetworkHealthService._last = [{"interface": "N2", "status": "ok"}]
            nh.NetworkHealthService._last_at = nh.time.monotonic()
            return list(nh.NetworkHealthService._last)

        with mock.patch.object(nh.NetworkHealthService, "run_health_checks", slow_run):
            threads = [threading.Thread(target=nh.NetworkHealthService(k8s=None).latest, args=(25,)) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(runs), 1)


if __name__ == "__main__":
    unittest.main()

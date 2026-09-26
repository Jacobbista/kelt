import unittest

from app.services.amf_cni_service import check_alert


class BrokenCore:
    def list_namespaced_pod(self, **kwargs):
        raise RuntimeError("api down")


class AlertErrorsTest(unittest.TestCase):
    def test_the_dashboard_view_hides_the_error(self):
        self.assertEqual(check_alert(type("K8s", (), {"core": BrokenCore()})()), {"active": False})

    def test_strict_mode_raises_so_the_caller_can_report_it(self):
        with self.assertRaises(RuntimeError):
            check_alert(type("K8s", (), {"core": BrokenCore()})(), strict=True)


if __name__ == "__main__":
    unittest.main()

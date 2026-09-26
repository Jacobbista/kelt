import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from app.services import k8s_service
from app.services import network_health_service as nh


def running_pod(name):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, deletion_timestamp=None),
        status=SimpleNamespace(phase="Running"),
    )


class ThreadClientTest(unittest.TestCase):
    """kubernetes.stream swaps `request` on the ApiClient it runs on for as long as
    the exec lasts, so a client shared by the check threads sends the other
    threads' plain API calls as websocket upgrades ("Handshake status 200 OK")."""

    def setUp(self):
        k8s_service._thread_local.__dict__.clear()
        patcher = mock.patch.object(k8s_service.config, "load_kube_config")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_one_client_per_thread(self):
        seen = []
        t = threading.Thread(target=lambda: seen.append(k8s_service.thread_core().api_client))
        t.start()
        t.join()
        here = k8s_service.thread_core().api_client
        self.assertIs(here, k8s_service.thread_core().api_client)
        self.assertIsNot(here, seen[0])

    def test_checks_do_not_use_the_shared_client(self):
        core = mock.Mock()
        core.list_namespaced_pod.return_value = SimpleNamespace(items=[running_pod("netshoot-1")])
        out = "1 packets transmitted, 1 received, 0% packet loss\nrtt time=0.4 ms"
        with mock.patch.object(nh, "thread_core", return_value=core), \
             mock.patch.object(nh, "stream", return_value=out):
            svc = nh.NetworkHealthService(k8s=None)
            for check in (svc._check_n3, svc._check_n6):
                self.assertEqual(check({"n3_gw": "10.203.0.1", "n6c_gw": "10.207.0.1"})["status"], "ok")


if __name__ == "__main__":
    unittest.main()

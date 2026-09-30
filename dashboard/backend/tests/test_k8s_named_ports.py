import io
import json
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from app.services.k8s_service import nf_api_get, pod_port


def pod(name, ports, phase="Running"):
    cports = [NS(name=n, container_port=p) for n, p in ports]
    return NS(metadata=NS(name=name, deletion_timestamp=None), status=NS(phase=phase),
              spec=NS(containers=[NS(ports=cports)]))


def core_with(*pods):
    core = mock.Mock()
    core.list_namespaced_pod.return_value = NS(items=list(pods))
    core.api_client.call_api.return_value = io.BytesIO(json.dumps({"items": []}).encode())
    return core


class NamedPortTest(unittest.TestCase):
    """NF ports are read by name from the running pod, where the NF is wired."""

    AMF = [("sbi", 7777), ("ngap", 38412), ("metrics", 9090)]

    def test_pod_port_by_name(self):
        self.assertEqual(pod_port(core_with(pod("amf-1", self.AMF)), "5g", "amf", "ngap"), 38412)

    def test_pod_port_unknown_name_or_no_pod(self):
        self.assertIsNone(pod_port(core_with(pod("amf-1", self.AMF)), "5g", "amf", "http"))
        self.assertIsNone(pod_port(core_with(), "5g", "amf", "ngap"))
        self.assertIsNone(pod_port(core_with(pod("amf-1", self.AMF, phase="Pending")), "5g", "amf", "ngap"))

    def test_nf_api_get_resolves_a_named_port(self):
        core = core_with(pod("amf-1", [("metrics", 9123)]))
        nf_api_get(core, "5g", "amf", "metrics", "gnb-info")
        path = core.api_client.call_api.call_args[0][0]
        self.assertEqual(path, "/api/v1/namespaces/5g/pods/amf-1:9123/proxy/gnb-info")

    def test_nf_api_get_without_the_named_port_is_empty(self):
        core = core_with(pod("amf-1", [("sbi", 7777)]))
        self.assertEqual(nf_api_get(core, "5g", "amf", "metrics", "gnb-info"), {})
        core.api_client.call_api.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class NfApiTimeoutTest(unittest.TestCase):
    def test_the_pod_proxy_call_has_a_short_timeout(self):
        # An NF being replaced must not hang the RAN page's status for minutes.
        from types import SimpleNamespace as NS
        from unittest import mock
        from app.services import k8s_service as ks
        pod = NS(metadata=NS(name="amf-1"), spec=NS(containers=[]))
        core = mock.Mock()
        core.api_client.call_api.return_value = NS(read=lambda: b"{}")
        with mock.patch.object(ks, "_running_pods", return_value=[pod]):
            ks.nf_api_get(core, "5g", "amf", 9090, "gnb-info")
        self.assertEqual(core.api_client.call_api.call_args.kwargs["_request_timeout"], ks.NF_API_TIMEOUT)
        self.assertLessEqual(sum(ks.NF_API_TIMEOUT), 8)

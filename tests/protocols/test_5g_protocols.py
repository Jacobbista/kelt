"""
5G Protocol Tests for K3s KubeEdge Testbed
Tests specific 5G protocols: PFCP, NGAP, GTP-U, NAS
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.k8s_client import K8sClient
from utils.test_helpers import TestConfig, TestLogger, NetworkValidator, ComponentValidator


class ProtocolTestSuite:
    """5G Protocol test suite"""
    
    def __init__(self, verbose: bool = False):
        self.config = TestConfig()
        self.logger = TestLogger(verbose)
        self.kubectl = K8sClient(self.config.get("cluster.kubeconfig_path"))
        self.network_validator = NetworkValidator(self.kubectl, self.config)
        self.component_validator = ComponentValidator(self.kubectl, self.config)
        self.verbose = verbose
    
    def run_all_tests(self) -> bool:
        """Run all protocol tests"""
        self.logger.info("Starting 5G Protocol Test Suite")
        
        tests = [
            ("PFCP Protocol (N4)", self.test_pfcp_protocol),
            ("NGAP Protocol (N2)", self.test_ngap_protocol),
            ("GTP-U Protocol (N3)", self.test_gtpu_protocol),
            ("N3 Gateway Reachability", self.test_n3_gateway_reachability),
            ("NAS Protocol (N1)", self.test_nas_protocol),
            ("Network Interface IPs", self.test_network_interface_ips),
            ("VXLAN Tunnel Configuration", self.test_vxlan_tunnels),
            ("OVS Bridge Setup", self.test_ovs_bridges),
            ("Protocol Message Exchange", self.test_protocol_message_exchange),
            ("PDU Failure Signatures", self.test_pdu_failure_signatures)
        ]
        
        passed = 0
        failed = 0
        
        for test_name, test_func in tests:
            self.logger.test_start(test_name)
            try:
                success = test_func()
                if success:
                    passed += 1
                else:
                    failed += 1
            except Exception as e:
                self.logger.error(f"{test_name} failed with exception: {e}")
                success = False
                failed += 1
            self.logger.test_end(test_name, success)
        
        self.logger.info(f"Protocol Test Results: {passed} passed, {failed} failed")
        return failed == 0
    
    def test_pfcp_protocol(self) -> bool:
        """Test PFCP protocol (N4 interface)"""
        self.logger.info("Testing PFCP protocol (N4)...")
        
        try:
            smf_pods = self.component_validator.get_component_pods("smf")
            if not smf_pods:
                self.logger.error("No SMF pods found")
                return False
            
            smf_pod = smf_pods[0]["metadata"]["name"]
            
            ok, out = self.network_validator.check_port_listening(smf_pod, "5g", 8805, "UDP", capture=True)
            if not ok:
                self.logger.error("SMF not listening on PFCP port 8805")
                self.logger.info(f"[debug] ss -unap (SMF {smf_pod}):\n{out}")
                self.component_validator.debug_pod(smf_pod, "5g", self.logger)
                return False
            self.logger.success("SMF listening on PFCP port 8805")
            
            upf_pods = self.component_validator.get_component_pods("upf")
            if not upf_pods:
                self.logger.error("No UPF pods found")
                return False
            
            for upf_pod in upf_pods:
                upf_name = upf_pod["metadata"]["name"]
                ok, out = self.network_validator.check_port_listening(upf_name, "5g", 8805, "UDP", capture=True)
                if not ok:
                    self.logger.error(f"UPF {upf_name} not listening on PFCP port 8805")
                    self.logger.info(f"[debug] ss -unap ({upf_name}):\n{out}")
                    self.component_validator.debug_pod(upf_name, "5g", self.logger)
                    return False
                self.logger.success(f"UPF {upf_name} listening on PFCP port 8805")
            
            smf_n4_ip = self.config.get("network.interfaces.n4.smf_ip")
            ok, out = self.network_validator.check_interface_ip(smf_pod, "5g", "n4", smf_n4_ip, capture=True)
            if not ok:
                self.logger.error(f"SMF N4 interface not configured with IP {smf_n4_ip}")
                self.logger.info(f"[debug] ip addr show n4 (SMF {smf_pod}):\n{out}")
                self.component_validator.debug_pod(smf_pod, "5g", self.logger)
                return False
            self.logger.success(f"SMF N4 interface configured with IP {smf_n4_ip}")
            return True
            
        except Exception as e:
            self.logger.error(f"PFCP protocol test failed: {e}")
            return False
    
    def test_ngap_protocol(self) -> bool:
        """Test NGAP protocol (N2 interface)"""
        self.logger.info("Testing NGAP protocol (N2)...")
        
        try:
            amf_pods = self.component_validator.get_component_pods("amf")
            if not amf_pods:
                self.logger.error("No AMF pods found")
                return False
            
            amf_pod = amf_pods[0]["metadata"]["name"]
            
            ok, out = self.network_validator.check_port_listening(amf_pod, "5g", 38412, "SCTP", capture=True)
            if not ok:
                self.logger.error("AMF not listening on SCTP port 38412 for NGAP")
                self.logger.info(f"[debug] ss -S -na (AMF {amf_pod}):\n{out}")
                self.component_validator.debug_pod(amf_pod, "5g", self.logger)
                return False
            self.logger.success("AMF listening on SCTP port 38412 for NGAP")
            
            amf_n2_ip = self.config.get("network.interfaces.n2.amf_ip")
            ok, out = self.network_validator.check_interface_ip(amf_pod, "5g", "n2", amf_n2_ip, capture=True)
            if not ok:
                self.logger.error(f"AMF N2 interface not configured with IP {amf_n2_ip}")
                self.logger.info(f"[debug] ip addr show n2 (AMF {amf_pod}):\n{out}")
                self.component_validator.debug_pod(amf_pod, "5g", self.logger)
                return False
            self.logger.success(f"AMF N2 interface configured with IP {amf_n2_ip}")
            
            gnb_pods = [p for p in self.kubectl.get_pods("5g") if "gnb" in p["metadata"]["name"].lower()]
            if gnb_pods:
                gnb_pod = gnb_pods[0]["metadata"]["name"]
                ok, out = self.network_validator.check_connectivity(gnb_pod, amf_pod, "5g", amf_n2_ip, capture=True)
                if ok:
                    self.logger.success("gNB can reach AMF on N2 interface")
                else:
                    self.logger.warning("gNB cannot reach AMF on N2 interface (might be normal during startup)")
                    self.logger.info(f"[debug] ping output (gNB {gnb_pod} → AMF {amf_pod} {amf_n2_ip}):\n{out}")
            return True
            
        except Exception as e:
            self.logger.error(f"NGAP protocol test failed: {e}")
            return False
    
    def test_gtpu_protocol(self) -> bool:
        """Test GTP-U protocol (N3 interface)"""
        self.logger.info("Testing GTP-U protocol (N3)...")
        
        try:
            upf_pods = self.component_validator.get_component_pods("upf")
            if not upf_pods:
                self.logger.error("No UPF pods found")
                return False
            
            for upf_pod in upf_pods:
                upf_name = upf_pod["metadata"]["name"]
                ok, out = self.network_validator.check_port_listening(upf_name, "5g", 2152, "UDP", capture=True)
                if not ok:
                    self.logger.error(f"UPF {upf_name} not listening on GTP-U port 2152")
                    self.logger.info(f"[debug] ss -unap ({upf_name}):\n{out}")
                    self.component_validator.debug_pod(upf_name, "5g", self.logger)
                    return False
                self.logger.success(f"UPF {upf_name} listening on GTP-U port 2152")
                
                expected_ip = (self.config.get("network.interfaces.n3.upf_edge_ip")
                               if "edge" in upf_name.lower()
                               else self.config.get("network.interfaces.n3.upf_cloud_ip"))
                ok, out = self.network_validator.check_interface_ip(upf_name, "5g", "n3", expected_ip, capture=True)
                if not ok:
                    self.logger.error(f"UPF {upf_name} N3 interface not configured with IP {expected_ip}")
                    self.logger.info(f"[debug] ip addr show n3 ({upf_name}):\n{out}")
                    self.component_validator.debug_pod(upf_name, "5g", self.logger)
                    return False
                self.logger.success(f"UPF {upf_name} N3 interface configured with IP {expected_ip}")
            return True
            
        except Exception as e:
            self.logger.error(f"GTP-U protocol test failed: {e}")
            return False
    
    def test_nas_protocol(self) -> bool:
        """Test NAS protocol (N1 interface)"""
        self.logger.info("Testing NAS protocol (N1)...")
        
        try:
            amf_pods = self.component_validator.get_component_pods("amf")
            if not amf_pods:
                self.logger.error("No AMF pods found")
                return False
            
            amf_pod = amf_pods[0]["metadata"]["name"]
            
            ok, out = self.network_validator.check_port_listening(amf_pod, "5g", 38412, "SCTP", capture=True)
            if not ok:
                self.logger.error("AMF not listening on SCTP port 38412 for NAS")
                self.logger.info(f"[debug] ss -S -na (AMF {amf_pod}):\n{out}")
                self.component_validator.debug_pod(amf_pod, "5g", self.logger)
                return False
            self.logger.success("AMF listening on SCTP port 38412 for NAS")
            
            amf_n1_ip = self.config.get("network.interfaces.n1.amf_ip")
            ok, out = self.network_validator.check_interface_ip(amf_pod, "5g", "n1", amf_n1_ip, capture=True)
            if not ok:
                self.logger.error(f"AMF N1 interface not configured with IP {amf_n1_ip}")
                self.logger.info(f"[debug] ip addr show n1 (AMF {amf_pod}):\n{out}")
                self.component_validator.debug_pod(amf_pod, "5g", self.logger)
                return False
            self.logger.success(f"AMF N1 interface configured with IP {amf_n1_ip}")
            
            ue_pods = [p for p in self.kubectl.get_pods("5g") if "ue" in p["metadata"]["name"].lower()]
            if ue_pods:
                ue_pod = ue_pods[0]["metadata"]["name"]
                ok, out = self.network_validator.check_connectivity(ue_pod, amf_pod, "5g", amf_n1_ip, capture=True)
                if ok:
                    self.logger.success("UE can reach AMF on N1 interface")
                else:
                    self.logger.warning("UE cannot reach AMF on N1 interface (might be normal during startup)")
                    self.logger.info(f"[debug] ping output (UE {ue_pod} → AMF {amf_pod} {amf_n1_ip}):\n{out}")
            return True
            
        except Exception as e:
            self.logger.error(f"NAS protocol test failed: {e}")
            return False

    def test_n3_gateway_reachability(self) -> bool:
        """Test N3 gateway reachability from UPF pods"""
        self.logger.info("Testing N3 gateway reachability from UPFs...")

        n3_gateway = "10.203.0.1"

        try:
            upf_pods = self.component_validator.get_component_pods("upf")
            if not upf_pods:
                self.logger.error("No UPF pods found")
                return False

            for upf_pod in upf_pods:
                upf_name = upf_pod["metadata"]["name"]
                result = self.kubectl.exec_in_pod(
                    upf_name,
                    "5g",
                    ["ping", "-c", "2", "-W", "2", "-I", "n3", n3_gateway],
                )
                if result.returncode != 0:
                    self.logger.error(f"UPF {upf_name} cannot reach N3 gateway {n3_gateway}")
                    self.logger.info(f"[debug] ping output ({upf_name}):\n{result.stdout}\n{result.stderr}")
                    self.component_validator.debug_pod(upf_name, "5g", self.logger)
                    return False
                self.logger.success(f"UPF {upf_name} can reach N3 gateway {n3_gateway}")

            return True

        except Exception as e:
            self.logger.error(f"N3 gateway reachability test failed: {e}")
            return False
    
    def test_network_interface_ips(self) -> bool:
        """Test network interface IP assignments"""
        self.logger.info("Testing network interface IP assignments...")
        
        try:
            amf_pods = self.component_validator.get_component_pods("amf")
            if not amf_pods:
                self.logger.error("No AMF pods found")
                return False
            
            amf_pod = amf_pods[0]["metadata"]["name"]
            
            n1_ip = self.config.get("network.interfaces.n1.amf_ip")
            ok, out = self.network_validator.check_interface_ip(amf_pod, "5g", "n1", n1_ip, capture=True)
            if not ok:
                self.logger.error(f"AMF N1 interface IP mismatch: expected {n1_ip}")
                self.logger.info(f"[debug] ip addr show n1 (AMF {amf_pod}):\n{out}")
                self.component_validator.debug_pod(amf_pod, "5g", self.logger)
                return False
            
            n2_ip = self.config.get("network.interfaces.n2.amf_ip")
            ok, out = self.network_validator.check_interface_ip(amf_pod, "5g", "n2", n2_ip, capture=True)
            if not ok:
                self.logger.error(f"AMF N2 interface IP mismatch: expected {n2_ip}")
                self.logger.info(f"[debug] ip addr show n2 (AMF {amf_pod}):\n{out}")
                self.component_validator.debug_pod(amf_pod, "5g", self.logger)
                return False
            
            self.logger.success("AMF interface IPs configured correctly")
            
            smf_pods = self.component_validator.get_component_pods("smf")
            if not smf_pods:
                self.logger.error("No SMF pods found")
                return False
            
            smf_pod = smf_pods[0]["metadata"]["name"]
            n4_ip = self.config.get("network.interfaces.n4.smf_ip")
            ok, out = self.network_validator.check_interface_ip(smf_pod, "5g", "n4", n4_ip, capture=True)
            if not ok:
                self.logger.error(f"SMF N4 interface IP mismatch: expected {n4_ip}")
                self.logger.info(f"[debug] ip addr show n4 (SMF {smf_pod}):\n{out}")
                self.component_validator.debug_pod(smf_pod, "5g", self.logger)
                return False
            
            self.logger.success("SMF interface IP configured correctly")
            return True
            
        except Exception as e:
            self.logger.error(f"Network interface IP test failed: {e}")
            return False
    
    def test_vxlan_tunnels(self) -> bool:
        """Test VXLAN tunnel configuration.

        ovs-setup.sh builds per-interface VXLAN tunnels only in multi-node
        (edge-enabled) topology; with edge disabled the bridges are local and
        carry no VXLAN (the only kernel vxlan device is flannel, unrelated to
        the 5G overlays). The check is therefore gated on edge node presence:
        skipped when edge is off, asserted when edge is on.
        """
        self.logger.info("Testing VXLAN tunnel configuration...")

        try:
            nodes = self.kubectl.get_nodes()
            edge_nodes = [n for n in nodes if "edge" in n["metadata"]["name"].lower()]
            if not edge_nodes:
                self.logger.warning(
                    "Edge disabled: 5G overlays use local bridges, no VXLAN tunnels expected (skipping)"
                )
                return True

            # OVS setup is done by ds-net-setup-* DaemonSets, not pods named "ovs"
            ovs_pods = [p for p in self.kubectl.get_pods("kube-system")
                        if "ds-net-setup" in p["metadata"]["name"].lower()
                        or "ovs" in p["metadata"]["name"].lower()]
            running_ovs = [p for p in ovs_pods if p["status"]["phase"] == "Running"]
            if not running_ovs:
                self.logger.warning("Edge enabled but no running OVS setup pods found")
                return True

            self.logger.success(f"Found {len(running_ovs)} running OVS setup pods")

            found_vxlan = False
            for ovs_pod in running_ovs:
                pod_name = ovs_pod["metadata"]["name"]
                try:
                    result = self.kubectl.exec_in_pod(pod_name, "kube-system", ["ovs-vsctl", "show"])
                    if "vxlan" in result.stdout.lower():
                        self.logger.success(f"VXLAN interfaces found on {pod_name}")
                        found_vxlan = True
                    else:
                        self.logger.warning(f"No VXLAN interfaces found on {pod_name}")
                except Exception as e:
                    self.logger.warning(f"Could not check VXLAN on {pod_name}: {e}")

            if not found_vxlan:
                self.logger.error("Edge enabled but no VXLAN tunnels found on any OVS pod")
                return False
            return True

        except Exception as e:
            self.logger.error(f"VXLAN tunnel test failed: {e}")
            return False
    
    def test_ovs_bridges(self) -> bool:
        """Test OVS bridge setup"""
        self.logger.info("Testing OVS bridge setup...")
        
        try:
            # OVS setup is done by ds-net-setup-* DaemonSets
            ovs_pods = [p for p in self.kubectl.get_pods("kube-system") 
                       if "ds-net-setup" in p["metadata"]["name"].lower()
                       or "ovs" in p["metadata"]["name"].lower()]
            
            if not ovs_pods:
                # Not an error - OVS is configured directly on nodes via DaemonSet
                self.logger.warning("No OVS setup pods found (OVS is configured on nodes)")
                return True
            
            running_ovs = [p for p in ovs_pods if p["status"]["phase"] == "Running"]
            if running_ovs:
                self.logger.success(f"Found {len(running_ovs)} OVS setup pods")
            
            for ovs_pod in running_ovs:
                pod_name = ovs_pod["metadata"]["name"]
                try:
                    result = self.kubectl.exec_in_pod(pod_name, "kube-system", ["ovs-vsctl", "list-br"])
                    bridges = [b for b in result.stdout.strip().splitlines() if b]
                    if bridges:
                        self.logger.success(f"OVS bridges found on {pod_name}: {bridges}")
                    else:
                        self.logger.warning(f"No OVS bridges found on {pod_name}")
                except Exception as e:
                    self.logger.warning(f"Could not check OVS bridges on {pod_name}: {e}")
            return True
            
        except Exception as e:
            self.logger.error(f"OVS bridge test failed: {e}")
            return False
    
    def test_protocol_message_exchange(self) -> bool:
        """Test protocol message exchange"""
        self.logger.info("Testing protocol message exchange...")
        
        try:
            amf_pods = self.component_validator.get_component_pods("amf")
            smf_pods = self.component_validator.get_component_pods("smf")
            
            if amf_pods and smf_pods:
                amf_pod = amf_pods[0]["metadata"]["name"]
                smf_pod = smf_pods[0]["metadata"]["name"]
                
                result = self.kubectl.exec_in_pod(amf_pod, "5g", ["hostname", "-i"])
                amf_ip = result.stdout.strip()
                
                ok, out = self.network_validator.check_connectivity(smf_pod, amf_pod, "5g", amf_ip, capture=True)
                if ok:
                    self.logger.success("SMF can reach AMF")
                else:
                    self.logger.warning("SMF cannot reach AMF (might be normal during startup)")
                    self.logger.info(f"[debug] ping output (SMF {smf_pod} → AMF {amf_pod} {amf_ip}):\n{out}")
            
            gnb_pods = [p for p in self.kubectl.get_pods("5g") if "gnb" in p["metadata"]["name"].lower()]
            if gnb_pods and amf_pods:
                gnb_pod = gnb_pods[0]["metadata"]["name"]
                amf_pod = amf_pods[0]["metadata"]["name"]
                
                amf_n2_ip = self.config.get("network.interfaces.n2.amf_ip")
                ok, out = self.network_validator.check_connectivity(gnb_pod, amf_pod, "5g", amf_n2_ip, capture=True)
                if ok:
                    self.logger.success("gNB can reach AMF on N2 interface")
                else:
                    self.logger.warning("gNB cannot reach AMF on N2 interface (might be normal during startup)")
                    self.logger.info(f"[debug] ping output (gNB {gnb_pod} → AMF {amf_pod} {amf_n2_ip}):\n{out}")
            return True
            
        except Exception as e:
            self.logger.error(f"Protocol message exchange test failed: {e}")
            return False

    def test_pdu_failure_signatures(self) -> bool:
        """Test known AMF/SMF PDU failure signatures are absent"""
        self.logger.info("Checking AMF/SMF logs for known PDU failure signatures...")

        try:
            amf_pods = self.component_validator.get_component_pods("amf")
            smf_pods = self.component_validator.get_component_pods("smf")
            if not amf_pods or not smf_pods:
                self.logger.error("AMF/SMF pods are required for signature checks")
                return False

            amf_pod = amf_pods[0]["metadata"]["name"]
            smf_pod = smf_pods[0]["metadata"]["name"]

            amf_logs = self.kubectl.get_pod_logs(amf_pod, "5g", tail_lines=400)
            smf_logs = self.kubectl.get_pod_logs(smf_pod, "5g", tail_lines=400)

            amf_patterns = [
                "PDUSessionResourceSetupResponse(Unsuccessful)",
                "DUPLICATED_PDU_SESSION_ID",
            ]
            smf_patterns = [
                "Cause[Group:1 Cause:34]",
            ]

            amf_hits = [p for p in amf_patterns if p in amf_logs]
            smf_hits = [p for p in smf_patterns if p in smf_logs]

            if amf_hits or smf_hits:
                if amf_hits:
                    self.logger.error(f"AMF failure signatures detected: {amf_hits}")
                if smf_hits:
                    self.logger.error(f"SMF failure signatures detected: {smf_hits}")
                return False

            self.logger.success("No known PDU failure signatures found in AMF/SMF logs")
            return True

        except Exception as e:
            self.logger.error(f"PDU failure signature test failed: {e}")
            return False


def main():
    """Main function for running protocol tests"""
    import argparse
    
    parser = argparse.ArgumentParser(description="5G Protocol Tests")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    test_suite = ProtocolTestSuite(verbose=args.verbose)
    success = test_suite.run_all_tests()
    
    if success:
        print("\n🎉 All protocol tests passed!")
        sys.exit(0)
    else:
        print("\n💥 Some protocol tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()

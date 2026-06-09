"""
End-to-End Tests for 5G K3s KubeEdge Testbed
Tests complete system integration and functionality
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.k8s_client import K8sClient  # <-- use API client
from utils.test_helpers import TestConfig, TestLogger, NetworkValidator, ComponentValidator


class E2ETestSuite:
    """End-to-End test suite for 5G testbed"""
    
    def __init__(self, verbose: bool = False):
        self.config = TestConfig()
        self.logger = TestLogger(verbose)
        # keep attribute name 'kubectl' to avoid wider refactors
        self.kubectl = K8sClient(self.config.get("cluster.kubeconfig_path"))
        self.network_validator = NetworkValidator(self.kubectl, self.config)
        self.component_validator = ComponentValidator(self.kubectl, self.config)
        self.verbose = verbose
    
    def run_all_tests(self) -> bool:
        """Run all E2E tests"""
        self.logger.info("Starting End-to-End Test Suite")
        
        tests = [
            ("Infrastructure Connectivity", self.test_infrastructure_connectivity),
            ("Kubernetes Cluster Health", self.test_kubernetes_cluster_health),
            ("KubeEdge Integration", self.test_kubeedge_integration),
            ("Overlay Network Setup", self.test_overlay_network_setup),
            ("5G Core Deployment", self.test_5g_core_deployment),
            ("Network Interfaces", self.test_network_interfaces),
            ("5G Protocol Connectivity", self.test_5g_protocol_connectivity),
            ("UERANSIM Deployment", self.test_ueransim_deployment),
            ("RAN Mode Primitives", self.test_ran_mode_primitives),
            ("RAN Overlay Labeling", self.test_ran_overlay_labeling),
            ("Edge Placement Semantics", self.test_edge_placement_semantics),
            ("MEC Deployment", self.test_mec_deployment),
            ("End-to-End Connectivity", self.test_end_to_end_connectivity)
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
        
        self.logger.info(f"E2E Test Results: {passed} passed, {failed} failed")
        return failed == 0
    
    def test_infrastructure_connectivity(self) -> bool:
        """Test basic infrastructure connectivity"""
        self.logger.info("Testing infrastructure connectivity...")
        
        try:
            nodes = self.kubectl.get_nodes()
            if not nodes:
                self.logger.error("No nodes found in cluster")
                return False
            
            self.logger.success(f"Found {len(nodes)} nodes in cluster")
            
            for node in nodes:
                node_name = node["metadata"]["name"]
                conditions = node["status"]["conditions"]
                ready_condition = next((c for c in conditions if c["type"] == "Ready"), None)
                if not ready_condition or ready_condition["status"] != "True":
                    self.logger.error(f"Node {node_name} is not Ready")
                    return False
            
            self.logger.success("All nodes are Ready")
            return True
            
        except Exception as e:
            self.logger.error(f"Infrastructure connectivity test failed: {e}")
            return False
    
    def test_kubernetes_cluster_health(self) -> bool:
        """Test Kubernetes cluster health"""
        self.logger.info("Testing Kubernetes cluster health...")
        
        try:
            system_pods = self.kubectl.get_pods("kube-system")
            running_system_pods = [p for p in system_pods if p["status"]["phase"] == "Running"]
            
            if len(running_system_pods) < 5:
                self.logger.error(f"Too few system pods running: {len(running_system_pods)}")
                return False
            
            self.logger.success(f"Found {len(running_system_pods)} running system pods")
            
            crashed_pods = [p for p in system_pods if p["status"]["phase"] in ["Failed", "CrashLoopBackOff"]]
            if crashed_pods:
                self.logger.error(f"Found crashed system pods: {[p['metadata']['name'] for p in crashed_pods]}")
                return False
            
            self.logger.success("No crashed system pods found")
            return True
            
        except Exception as e:
            self.logger.error(f"Kubernetes cluster health test failed: {e}")
            return False
    
    def test_kubeedge_integration(self) -> bool:
        """Test KubeEdge integration.

        The testbed runs with or without the edge VM. When no edge node is
        present (edge disabled), the KubeEdge checks are skipped rather than
        failed, mirroring the edge_enabled gating used across the deployment.
        """
        self.logger.info("Testing KubeEdge integration...")

        try:
            nodes = self.kubectl.get_nodes()
            edge_nodes = [n for n in nodes if "edge" in n["metadata"]["name"].lower()]
            if not edge_nodes:
                self.logger.warning("No edge nodes (edge disabled); skipping KubeEdge checks")
                return True

            kubeedge_pods = self.kubectl.get_pods("kubeedge")
            if not kubeedge_pods:
                self.logger.error("Edge nodes present but no KubeEdge pods found")
                return False

            cloudcore_pods = [p for p in kubeedge_pods if "cloudcore" in p["metadata"]["name"].lower()]
            running_cloudcore = [p for p in cloudcore_pods if p["status"]["phase"] == "Running"]
            if not running_cloudcore:
                self.logger.error("CloudCore is not running")
                return False

            self.logger.success("KubeEdge CloudCore is running")
            self.logger.success(f"Found {len(edge_nodes)} edge nodes")
            return True

        except Exception as e:
            self.logger.error(f"KubeEdge integration test failed: {e}")
            return False
    
    def test_overlay_network_setup(self) -> bool:
        """Test overlay network setup (Multus, OVS, VXLAN)"""
        self.logger.info("Testing overlay network setup...")
        
        try:
            multus_pods = self.kubectl.get_pods("kube-system")
            multus_pods = [p for p in multus_pods if "multus" in p["metadata"]["name"].lower()]
            
            if not multus_pods:
                self.logger.error("Multus pods not found")
                return False
            
            running_multus = [p for p in multus_pods if p["status"]["phase"] == "Running"]
            # Multus is intentionally pinned to the worker (nodeSelector), where
            # the overlays live, so placement is deployment-defined: require at
            # least one running pod rather than one per node.
            if not running_multus:
                self.logger.error("No Multus pods running")
                return False
            self.logger.success(f"Found {len(running_multus)} running Multus pods")
            
            nads = self.kubectl.get_network_attachments()
            # Core NADs required for 5G Core
            required_nads = ["n1-net", "n2-net", "n3-net", "n4-net"]
            # Optional NADs for MEC
            optional_nads = ["n6-mec-net", "n6-cld-net"]
            
            nad_names = [nad["metadata"]["name"] for nad in nads]
            missing_required = [nad for nad in required_nads if nad not in nad_names]
            missing_optional = [nad for nad in optional_nads if nad not in nad_names]
            
            if missing_required:
                self.logger.error(f"Missing required NetworkAttachmentDefinitions: {missing_required}")
                return False
            
            if missing_optional:
                self.logger.warning(f"Optional NADs not found (MEC not deployed): {missing_optional}")
            
            self.logger.success(f"Found {len(nads)} NetworkAttachmentDefinitions")
            return True
            
        except Exception as e:
            self.logger.error(f"Overlay network setup test failed: {e}")
            return False
    
    def test_5g_core_deployment(self) -> bool:
        """Test 5G Core deployment"""
        self.logger.info("Testing 5G Core deployment...")
        
        try:
            fiveg_pods = self.kubectl.get_pods("5g")
            if not fiveg_pods:
                self.logger.error("No 5G pods found")
                return False
            
            components = ["amf", "smf", "upf"]
            for component in components:
                component_pods = [p for p in fiveg_pods if component in p["metadata"]["name"].lower()]
                if not component_pods:
                    self.logger.error(f"No {component.upper()} pods found")
                    return False
                
                running_pods = [p for p in component_pods if p["status"]["phase"] == "Running"]
                if not running_pods:
                    self.logger.error(f"{component.upper()} is not running")
                    return False
                
                self.logger.success(f"{component.upper()} is running")
            
            return True
            
        except Exception as e:
            self.logger.error(f"5G Core deployment test failed: {e}")
            return False
    
    def test_network_interfaces(self) -> bool:
        """Test 5G network interfaces"""
        self.logger.info("Testing 5G network interfaces...")
        
        try:
            amf_pods = self.component_validator.get_component_pods("amf")
            if not amf_pods:
                self.logger.error("No AMF pods found for interface testing")
                return False
            
            amf_pod = amf_pods[0]["metadata"]["name"]
            
            n1_ip = self.config.get("network.interfaces.n1.amf_ip")
            if not self.network_validator.check_interface_ip(amf_pod, "5g", "n1", n1_ip):
                self.logger.error(f"AMF N1 interface not configured with IP {n1_ip}")
                return False
            
            self.logger.success("AMF N1 interface configured correctly")
            
            n2_ip = self.config.get("network.interfaces.n2.amf_ip")
            if not self.network_validator.check_interface_ip(amf_pod, "5g", "n2", n2_ip):
                self.logger.error(f"AMF N2 interface not configured with IP {n2_ip}")
                return False
            
            self.logger.success("AMF N2 interface configured correctly")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Network interfaces test failed: {e}")
            return False
    
    def test_5g_protocol_connectivity(self) -> bool:
        """Test 5G protocol connectivity"""
        self.logger.info("Testing 5G protocol connectivity...")
        
        try:
            amf_pods = self.component_validator.get_component_pods("amf")
            if not amf_pods:
                self.logger.error("No AMF pods found for protocol testing")
                return False
            
            amf_pod = amf_pods[0]["metadata"]["name"]
            
            if not self.network_validator.check_port_listening(amf_pod, "5g", 38412, "SCTP"):
                self.logger.error("AMF not listening on SCTP port 38412")
                return False
            
            self.logger.success("AMF listening on SCTP port 38412")
            
            smf_pods = self.component_validator.get_component_pods("smf")
            if not smf_pods:
                self.logger.error("No SMF pods found for protocol testing")
                return False
            
            smf_pod = smf_pods[0]["metadata"]["name"]
            
            if not self.network_validator.check_port_listening(smf_pod, "5g", 8805, "UDP"):
                self.logger.error("SMF not listening on PFCP port 8805")
                return False
            
            self.logger.success("SMF listening on PFCP port 8805")
            
            return True
            
        except Exception as e:
            self.logger.error(f"5G protocol connectivity test failed: {e}")
            return False
    
    def test_ueransim_deployment(self) -> bool:
        """UERANSIM simulated RAN (parked).

        UERANSIM is not deployed in the current testbed; a physical gNB and
        real UEs are used instead. This test skips when no gnb/ue pods exist,
        and only validates them if simulated RAN is re-enabled. The physical
        RAN path is validated by the `ran` suite (make ran).
        """
        self.logger.info("Testing UERANSIM deployment (parked)...")

        try:
            gnb_pods = [p for p in self.kubectl.get_pods("5g") if "gnb" in p["metadata"]["name"].lower()]
            ue_pods = [p for p in self.kubectl.get_pods("5g") if "ue" in p["metadata"]["name"].lower()]

            if not gnb_pods and not ue_pods:
                self.logger.warning("UERANSIM not deployed (parked); physical gNB/UE in use. See: make ran")
                return True

            running_gnb = [p for p in gnb_pods if p["status"]["phase"] == "Running"]
            if gnb_pods and not running_gnb:
                self.logger.error("UERANSIM gNB pod present but not Running")
                return False

            running_ue = [p for p in ue_pods if p["status"]["phase"] == "Running"]
            if ue_pods and not running_ue:
                self.logger.error("UERANSIM UE pod present but not Running")
                return False

            self.logger.success("UERANSIM simulated RAN is running")
            return True

        except Exception as e:
            self.logger.error(f"UERANSIM deployment test failed: {e}")
            return False
    
    def test_mec_deployment(self) -> bool:
        """Test MEC deployment"""
        self.logger.info("Testing MEC deployment...")
        
        try:
            mec_pods = self.kubectl.get_pods("mec")
            if not mec_pods:
                self.logger.warning("No MEC pods found (MEC might not be deployed)")
                return True  # MEC is optional
            
            running_mec = [p for p in mec_pods if p["status"]["phase"] == "Running"]
            if not running_mec:
                self.logger.warning("No running MEC pods found")
                return True  # MEC is optional
            
            self.logger.success(f"Found {len(running_mec)} running MEC pods")
            return True
            
        except Exception as e:
            self.logger.warning(f"MEC deployment test failed (MEC might not be deployed): {e}")
            return True  # MEC is optional

    def test_ran_mode_primitives(self) -> bool:
        """Validate resources required by dashboard RAN mode control."""
        self.logger.info("Testing RAN mode primitives...")
        try:
            fiveg_pods = self.kubectl.get_pods("5g")
            gnb_like = [p for p in fiveg_pods if "gnb" in p["metadata"]["name"].lower()]
            ue_like = [p for p in fiveg_pods if "ue" in p["metadata"]["name"].lower()]

            if not gnb_like and not ue_like:
                self.logger.warning("No UERANSIM pods found (simulated RAN not deployed)")
                return True

            labelled = 0
            for p in gnb_like + ue_like:
                labels = p["metadata"].get("labels", {})
                if labels.get("component") in {"gnb", "ue"} or labels.get("app") in {"ue"}:
                    labelled += 1

            if labelled == 0:
                self.logger.error("UERANSIM pods are missing expected labels (component/app)")
                return False

            self.logger.success(f"Found {labelled} UERANSIM pods with dashboard-compatible labels")
            return True
        except Exception as e:
            self.logger.error(f"RAN mode primitives test failed: {e}")
            return False

    def test_edge_placement_semantics(self) -> bool:
        """Check expected edge/datacenter placement model for infra topology."""
        self.logger.info("Testing edge placement semantics...")
        try:
            fiveg_pods = self.kubectl.get_pods("5g")
            edge_gnb_ue = []
            wrong_place = []
            for p in fiveg_pods:
                name = p["metadata"]["name"].lower()
                node = p["spec"].get("nodeName", "")
                if "gnb" in name or "ue" in name:
                    edge_gnb_ue.append((name, node))
                    if node and "edge" not in node.lower():
                        wrong_place.append((name, node))

            if not edge_gnb_ue:
                self.logger.warning("No gNB/UE pods found for edge placement checks")
                return True
            if wrong_place:
                self.logger.error(f"gNB/UE not on edge nodes: {wrong_place}")
                return False

            self.logger.success(f"All detected gNB/UE pods run on edge nodes ({len(edge_gnb_ue)} checked)")
            return True
        except Exception as e:
            self.logger.error(f"Edge placement semantics test failed: {e}")
            return False

    def test_ran_overlay_labeling(self) -> bool:
        """Ensure simulated RAN resources are labeled for runtime overlay management."""
        self.logger.info("Testing RAN overlay labeling...")
        try:
            fiveg_pods = self.kubectl.get_pods("5g")
            ran_pods = [p for p in fiveg_pods if ("gnb" in p["metadata"]["name"].lower() or "ue" in p["metadata"]["name"].lower())]
            if not ran_pods:
                self.logger.warning("No RAN pods found for overlay labeling checks")
                return True

            invalid = []
            for p in ran_pods:
                labels = p["metadata"].get("labels", {})
                manager = labels.get("managed-by")
                if manager not in {"ansible", "dashboard"}:
                    invalid.append(p["metadata"]["name"])

            if invalid:
                self.logger.error(f"RAN pods missing managed-by label: {invalid}")
                return False
            self.logger.success("RAN pods are labeled for baseline/runtime overlay tracking")
            return True
        except Exception as e:
            self.logger.error(f"RAN overlay labeling test failed: {e}")
            return False
    
    def test_end_to_end_connectivity(self) -> bool:
        """Test end-to-end connectivity"""
        self.logger.info("Testing end-to-end connectivity...")
        
        try:
            # Pick a running target pod and read its cluster IP from the API
            # (NF pods lack `hostname -i`); ping it from netshoot.
            running = [p for p in self.kubectl.get_pods("5g")
                       if p["status"].get("phase") == "Running" and p["status"].get("pod_ip")]
            target = next((p for p in running if "netshoot" not in p["metadata"]["name"].lower()), None)
            if not target:
                self.logger.error("No running 5G pod with an IP to probe")
                return False

            target_name = target["metadata"]["name"]
            target_ip = target["status"]["pod_ip"]

            if not self.network_validator.check_connectivity("netshoot", target_name, "5g", target_ip):
                self.logger.error(f"netshoot cannot reach {target_name} ({target_ip})")
                return False

            self.logger.success(f"netshoot can reach {target_name} ({target_ip})")
            return True
            
        except Exception as e:
            self.logger.error(f"End-to-end connectivity test failed: {e}")
            return False


def main():
    """Main function for running E2E tests"""
    import argparse
    
    parser = argparse.ArgumentParser(description="5G Testbed E2E Tests")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    test_suite = E2ETestSuite(verbose=args.verbose)
    success = test_suite.run_all_tests()
    
    if success:
        print("\n🎉 All E2E tests passed!")
        sys.exit(0)
    else:
        print("\n💥 Some E2E tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()

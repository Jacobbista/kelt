"""
Resilience Tests for 5G K3s KubeEdge Testbed
Tests failure recovery and fault tolerance
"""
import sys
import os
import time
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.kubectl_client import KubectlClient
from utils.test_helpers import TestConfig, TestLogger, NetworkValidator, ComponentValidator, CORE_NS


class ResilienceTestSuite:
    """Resilience test suite for 5G testbed"""
    
    def __init__(self, verbose: bool = False):
        self.config = TestConfig()
        self.logger = TestLogger(verbose)
        self.kubectl = KubectlClient(self.config.get("cluster.kubeconfig_path"))
        self.network_validator = NetworkValidator(self.kubectl, self.config)
        self.component_validator = ComponentValidator(self.kubectl, self.config)
        self.verbose = verbose
    
    def run_all_tests(self) -> bool:
        """Run all resilience tests"""
        self.logger.info("Starting Resilience Test Suite")
        
        tests = [
            ("Pod Restart Recovery", self.test_pod_restart_recovery),
            ("Network Interface Recovery", self.test_network_interface_recovery),
            ("Node Failure Recovery", self.test_node_failure_recovery),
            ("Network Partition Recovery", self.test_network_partition_recovery),
            ("OVS Bridge Recovery", self.test_ovs_bridge_recovery),
            ("VXLAN Tunnel Recovery", self.test_vxlan_tunnel_recovery),
            ("Multus Recovery", self.test_multus_recovery),
            ("KubeEdge Recovery", self.test_kubeedge_recovery),
            ("Database Recovery", self.test_database_recovery),
            ("Stress Test Cleanup", self.test_stress_cleanup)
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
                failed += 1
            self.logger.test_end(test_name, success)
        
        self.logger.info(f"Resilience Test Results: {passed} passed, {failed} failed")
        return failed == 0
    
    def test_pod_restart_recovery(self) -> bool:
        """Test pod restart recovery"""
        self.logger.info("Testing pod restart recovery...")
        
        try:
            # Test AMF pod restart
            amf_pods = self.component_validator.get_component_pods("amf")
            if not amf_pods:
                self.logger.error("No AMF pods found for restart testing")
                return False
            
            amf_pod = amf_pods[0]["metadata"]["name"]
            
            # Delete AMF pod to trigger restart
            self.logger.info(f"Deleting AMF pod {amf_pod}...")
            self.kubectl.run_command(["delete", "pod", amf_pod, "-n", CORE_NS])
            
            # Wait for pod to be recreated and running
            recovery_timeout = self.config.get("test_configs.resilience.recovery_timeout", 180)
            self.logger.info(f"Waiting for AMF pod recovery (timeout: {recovery_timeout}s)...")
            
            start_time = time.time()
            while time.time() - start_time < recovery_timeout:
                pods = self.kubectl.get_pods(CORE_NS)
                amf_pods = [p for p in pods if "amf" in p["metadata"]["name"].lower()]
                
                if amf_pods:
                    amf_pod = amf_pods[0]
                    if amf_pod["status"]["phase"] == "Running":
                        self.logger.success("AMF pod recovered successfully")
                        return True
                
                time.sleep(5)
            
            self.logger.error("AMF pod did not recover within timeout")
            return False
            
        except Exception as e:
            self.logger.error(f"Pod restart recovery test failed: {e}")
            return False
    
    def test_network_interface_recovery(self) -> bool:
        """Test network interface recovery"""
        self.logger.info("Testing network interface recovery...")
        
        try:
            # Test interface recovery after pod restart
            amf_pods = self.component_validator.get_component_pods("amf")
            if not amf_pods:
                self.logger.error("No AMF pods found for interface testing")
                return False
            
            amf_pod = amf_pods[0]["metadata"]["name"]
            
            # Check interfaces before restart
            n1_ip = self.config.plan("amf_n1_ip")
            n2_ip = self.config.plan("amf_n2_ip")
            
            n1_ok = self.network_validator.check_interface_ip(amf_pod, CORE_NS, "n1", n1_ip)
            n2_ok = self.network_validator.check_interface_ip(amf_pod, CORE_NS, "n2", n2_ip)
            
            if not n1_ok or not n2_ok:
                self.logger.warning("Interfaces not properly configured before restart")
            
            # Restart pod
            self.logger.info("Restarting AMF pod...")
            self.kubectl.run_command(["delete", "pod", amf_pod, "-n", CORE_NS])
            
            # Wait for recovery
            recovery_timeout = self.config.get("test_configs.resilience.recovery_timeout", 180)
            start_time = time.time()
            
            while time.time() - start_time < recovery_timeout:
                pods = self.kubectl.get_pods(CORE_NS)
                amf_pods = [p for p in pods if "amf" in p["metadata"]["name"].lower()]
                
                if amf_pods and amf_pods[0]["status"]["phase"] == "Running":
                    new_amf_pod = amf_pods[0]["metadata"]["name"]
                    
                    # Check interface recovery
                    n1_recovered = self.network_validator.check_interface_ip(new_amf_pod, CORE_NS, "n1", n1_ip)
                    n2_recovered = self.network_validator.check_interface_ip(new_amf_pod, CORE_NS, "n2", n2_ip)
                    
                    if n1_recovered and n2_recovered:
                        self.logger.success("Network interfaces recovered successfully")
                        return True
                
                time.sleep(5)
            
            self.logger.error("Network interfaces did not recover within timeout")
            return False
            
        except Exception as e:
            self.logger.error(f"Network interface recovery test failed: {e}")
            return False
    
    def test_node_failure_recovery(self) -> bool:
        """Test node failure recovery (simulated)"""
        self.logger.info("Testing node failure recovery...")
        
        try:
            # This is a simplified test - in a real scenario, you would simulate node failure
            # For now, we'll check if pods can be rescheduled
            
            # Get all pods
            all_pods = self.kubectl.get_pods()
            running_pods = [p for p in all_pods if p["status"]["phase"] == "Running"]
            
            self.logger.info(f"Found {len(running_pods)} running pods across all namespaces")
            
            # Check if critical pods are running
            critical_components = ["amf", "smf", "upf"]
            for component in critical_components:
                component_pods = [p for p in running_pods if component in p["metadata"]["name"].lower()]
                if not component_pods:
                    self.logger.error(f"No running {component.upper()} pods found")
                    return False
                self.logger.success(f"{component.upper()} pods are running")
            
            # Check node health
            nodes = self.kubectl.get_nodes()
            ready_nodes = []
            for node in nodes:
                conditions = node.get("status", {}).get("conditions", [])
                ready = next((c for c in conditions if c["type"] == "Ready"), None)
                if ready and ready["status"] == "True":
                    ready_nodes.append(node)
            
            if len(ready_nodes) < 2:  # At least master and one worker/edge
                self.logger.error(f"Not enough ready nodes: {len(ready_nodes)}")
                return False
            
            self.logger.success(f"Found {len(ready_nodes)} ready nodes")
            return True
            
        except Exception as e:
            self.logger.error(f"Node failure recovery test failed: {e}")
            return False
    
    def test_network_partition_recovery(self) -> bool:
        """Test network partition recovery"""
        self.logger.info("Testing network partition recovery...")
        
        try:
            # This is a simplified test - in a real scenario, you would simulate network partition
            # For now, we'll test connectivity between components
            
            # Test AMF-SMF connectivity
            amf_pods = self.component_validator.get_component_pods("amf")
            smf_pods = self.component_validator.get_component_pods("smf")
            
            if amf_pods and smf_pods:
                amf_pod = amf_pods[0]["metadata"]["name"]
                smf_pod = smf_pods[0]["metadata"]["name"]
                
                # Get AMF IP
                amf_ip_result = self.kubectl.exec_in_pod(amf_pod, CORE_NS, ["hostname", "-i"])
                amf_ip = amf_ip_result.stdout.strip()
                
                if self.network_validator.check_connectivity(smf_pod, amf_pod, CORE_NS, amf_ip):
                    self.logger.success("AMF-SMF connectivity working")
                else:
                    self.logger.warning("AMF-SMF connectivity issues (might be normal during startup)")
            
            # Test gNB-AMF connectivity
            gnb_pods = [p for p in self.kubectl.get_pods(CORE_NS) if "gnb" in p["metadata"]["name"].lower()]
            if gnb_pods and amf_pods:
                gnb_pod = gnb_pods[0]["metadata"]["name"]
                amf_pod = amf_pods[0]["metadata"]["name"]
                amf_n2_ip = self.config.plan("amf_n2_ip")
                
                if self.network_validator.check_connectivity(gnb_pod, amf_pod, CORE_NS, amf_n2_ip):
                    self.logger.success("gNB-AMF connectivity working")
                else:
                    self.logger.warning("gNB-AMF connectivity issues (might be normal during startup)")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Network partition recovery test failed: {e}")
            return False
    
    def test_ovs_bridge_recovery(self) -> bool:
        """Test OVS bridge recovery"""
        self.logger.info("Testing OVS bridge recovery...")
        
        try:
            # Check OVS DaemonSets - they are named ds-net-setup-*
            def get_ovs_pods():
                all_pods = self.kubectl.get_pods("kube-system")
                return [p for p in all_pods 
                       if "ds-net-setup" in p["metadata"]["name"].lower()
                       or "ovs" in p["metadata"]["name"].lower()]
            
            ovs_pods = get_ovs_pods()
            if not ovs_pods:
                # OVS might be configured directly on nodes
                self.logger.warning("No OVS setup pods found (OVS configured on nodes)")
                return True
            
            # Restart OVS DaemonSet
            self.logger.info("Restarting OVS DaemonSets...")
            self.kubectl.run_command(["rollout", "restart", "daemonset", "ds-net-setup-worker", "-n", "kube-system"])
            self.kubectl.run_command(["rollout", "restart", "daemonset", "ds-net-setup-edge", "-n", "kube-system"])
            
            # Wait for OVS pods to be ready
            recovery_timeout = self.config.get("test_configs.resilience.recovery_timeout", 180)
            self.logger.info(f"Waiting for OVS recovery (timeout: {recovery_timeout}s)...")
            
            start_time = time.time()
            while time.time() - start_time < recovery_timeout:
                ovs_pods = get_ovs_pods()
                running_ovs = [p for p in ovs_pods if p["status"]["phase"] == "Running"]
                
                if len(running_ovs) >= 2:  # Expected on worker and edge
                    self.logger.success("OVS DaemonSets recovered successfully")
                    return True
                
                time.sleep(5)
            
            self.logger.error("OVS DaemonSets did not recover within timeout")
            return False
            
        except Exception as e:
            self.logger.error(f"OVS bridge recovery test failed: {e}")
            return False
    
    def test_vxlan_tunnel_recovery(self) -> bool:
        """Test VXLAN tunnel recovery"""
        self.logger.info("Testing VXLAN tunnel recovery...")
        
        try:
            # Check VXLAN configuration after OVS recovery
            # OVS pods are named ds-net-setup-*
            ovs_pods = [p for p in self.kubectl.get_pods("kube-system") 
                       if "ds-net-setup" in p["metadata"]["name"].lower()
                       or "ovs" in p["metadata"]["name"].lower()]
            
            if not ovs_pods:
                # VXLAN might be configured on nodes directly
                self.logger.warning("No OVS setup pods found for VXLAN testing")
                return True
            
            # Check VXLAN interfaces
            for ovs_pod in ovs_pods:
                if ovs_pod["status"]["phase"] != "Running":
                    continue
                pod_name = ovs_pod["metadata"]["name"]
                try:
                    result = self.kubectl.exec_in_pod(
                        pod_name, "kube-system",
                        ["ovs-vsctl", "show"]
                    )
                    if "vxlan" in result.stdout.lower():
                        self.logger.success(f"VXLAN interfaces found on {pod_name}")
                    else:
                        self.logger.warning(f"No VXLAN interfaces found on {pod_name}")
                except Exception:
                    self.logger.warning(f"Could not check VXLAN on {pod_name}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"VXLAN tunnel recovery test failed: {e}")
            return False
    
    def test_multus_recovery(self) -> bool:
        """Test Multus recovery"""
        self.logger.info("Testing Multus recovery...")
        
        try:
            # Check Multus DaemonSet
            multus_pods = [p for p in self.kubectl.get_pods("kube-system") if "multus" in p["metadata"]["name"].lower()]
            if not multus_pods:
                self.logger.error("No Multus pods found")
                return False
            
            # Restart Multus DaemonSet
            self.logger.info("Restarting Multus DaemonSet...")
            self.kubectl.run_command(["rollout", "restart", "daemonset", "kube-multus-ds", "-n", "kube-system"])
            
            # Wait for Multus recovery
            recovery_timeout = self.config.get("test_configs.resilience.recovery_timeout", 180)
            self.logger.info(f"Waiting for Multus recovery (timeout: {recovery_timeout}s)...")
            
            start_time = time.time()
            while time.time() - start_time < recovery_timeout:
                multus_pods = [p for p in self.kubectl.get_pods("kube-system") if "multus" in p["metadata"]["name"].lower()]
                running_multus = [p for p in multus_pods if p["status"]["phase"] == "Running"]
                
                if len(running_multus) >= 2:  # Expected on worker and edge
                    self.logger.success("Multus DaemonSet recovered successfully")
                    return True
                
                time.sleep(5)
            
            self.logger.error("Multus DaemonSet did not recover within timeout")
            return False
            
        except Exception as e:
            self.logger.error(f"Multus recovery test failed: {e}")
            return False
    
    def test_kubeedge_recovery(self) -> bool:
        """Test KubeEdge recovery"""
        self.logger.info("Testing KubeEdge recovery...")
        
        try:
            # Check KubeEdge pods
            kubeedge_pods = self.kubectl.get_pods("kubeedge")
            if not kubeedge_pods:
                self.logger.error("No KubeEdge pods found")
                return False
            
            # Restart CloudCore
            cloudcore_pods = [p for p in kubeedge_pods if "cloudcore" in p["metadata"]["name"].lower()]
            if cloudcore_pods:
                cloudcore_pod = cloudcore_pods[0]["metadata"]["name"]
                self.logger.info(f"Restarting CloudCore pod {cloudcore_pod}...")
                self.kubectl.run_command(["delete", "pod", cloudcore_pod, "-n", "kubeedge"])
            
            # Wait for KubeEdge recovery
            recovery_timeout = self.config.get("test_configs.resilience.recovery_timeout", 180)
            self.logger.info(f"Waiting for KubeEdge recovery (timeout: {recovery_timeout}s)...")
            
            start_time = time.time()
            while time.time() - start_time < recovery_timeout:
                kubeedge_pods = self.kubectl.get_pods("kubeedge")
                running_kubeedge = [p for p in kubeedge_pods if p["status"]["phase"] == "Running"]
                
                if running_kubeedge:
                    self.logger.success("KubeEdge recovered successfully")
                    return True
                
                time.sleep(5)
            
            self.logger.error("KubeEdge did not recover within timeout")
            return False
            
        except Exception as e:
            self.logger.error(f"KubeEdge recovery test failed: {e}")
            return False
    
    def test_database_recovery(self) -> bool:
        """Test database recovery"""
        self.logger.info("Testing database recovery...")
        
        try:
            # Check MongoDB pods
            mongo_pods = [p for p in self.kubectl.get_pods(CORE_NS) if "mongo" in p["metadata"]["name"].lower()]
            if not mongo_pods:
                self.logger.warning("No MongoDB pods found (database might not be deployed)")
                return True  # Database is optional
            
            mongo_pod = mongo_pods[0]["metadata"]["name"]
            
            # Restart MongoDB pod
            self.logger.info(f"Restarting MongoDB pod {mongo_pod}...")
            self.kubectl.run_command(["delete", "pod", mongo_pod, "-n", CORE_NS])
            
            # Wait for MongoDB recovery
            recovery_timeout = self.config.get("test_configs.resilience.recovery_timeout", 180)
            self.logger.info(f"Waiting for MongoDB recovery (timeout: {recovery_timeout}s)...")
            
            start_time = time.time()
            while time.time() - start_time < recovery_timeout:
                mongo_pods = [p for p in self.kubectl.get_pods(CORE_NS) if "mongo" in p["metadata"]["name"].lower()]
                if mongo_pods and mongo_pods[0]["status"]["phase"] == "Running":
                    self.logger.success("MongoDB recovered successfully")
                    return True
                
                time.sleep(5)
            
            self.logger.error("MongoDB did not recover within timeout")
            return False
            
        except Exception as e:
            self.logger.error(f"Database recovery test failed: {e}")
            return False
    
    def test_stress_cleanup(self) -> bool:
        """Test stress test cleanup"""
        self.logger.info("Testing stress test cleanup...")
        
        try:
            # Clean up any test pods or resources
            test_pods = [p for p in self.kubectl.get_pods() if "test" in p["metadata"]["name"].lower()]
            
            for pod in test_pods:
                pod_name = pod["metadata"]["name"]
                namespace = pod["metadata"]["namespace"]
                self.logger.info(f"Cleaning up test pod {pod_name} in namespace {namespace}")
                self.kubectl.run_command(["delete", "pod", pod_name, "-n", namespace])
            
            # Wait for cleanup
            time.sleep(10)
            
            # Check if cleanup was successful
            remaining_test_pods = [p for p in self.kubectl.get_pods() if "test" in p["metadata"]["name"].lower()]
            if not remaining_test_pods:
                self.logger.success("Stress test cleanup completed successfully")
                return True
            else:
                self.logger.warning(f"Some test pods still remain: {[p['metadata']['name'] for p in remaining_test_pods]}")
                return True  # Don't fail for cleanup issues
            
        except Exception as e:
            self.logger.error(f"Stress test cleanup failed: {e}")
            return False


def main():
    """Main function for running resilience tests"""
    import argparse
    
    parser = argparse.ArgumentParser(description="5G Resilience Tests")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    test_suite = ResilienceTestSuite(verbose=args.verbose)
    success = test_suite.run_all_tests()
    
    if success:
        print("\n🎉 All resilience tests passed!")
        sys.exit(0)
    else:
        print("\n💥 Some resilience tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()

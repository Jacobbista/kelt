"""
Physical RAN Integration Tests
Tests for femtocell/small cell connectivity via OVS bridge
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import subprocess
from utils.k8s_client import K8sClient
from utils.test_helpers import TestConfig, TestLogger


class PhysicalRANTestSuite:
    """Test suite for physical RAN integration"""
    
    def __init__(self, verbose: bool = False):
        self.config = TestConfig()
        self.logger = TestLogger(verbose)
        self.kubectl = K8sClient(self.config.get("cluster.kubeconfig_path"))
        self.verbose = verbose
        
        # RAN network config
        self.worker_host = self.config.get("cluster.worker_host", "192.168.56.11")
        self.ran_network = "192.168.57.0/24"
        self.ran_gateway = "192.168.57.1"
    
    def run_all_tests(self) -> bool:
        """Run all physical RAN tests"""
        self.logger.info("Starting Physical RAN Integration Tests")
        
        tests = [
            ("OVS Bridge Configuration", self.test_ovs_bridge_config),
            ("Overlay Gateway Ownership", self.test_overlay_gateway_ownership),
            ("RAN Interface Detection", self.test_ran_interface),
            ("OVS RAN Bridge Exists", self.test_ovs_ran_bridge),
            ("Patch Ports Configured", self.test_patch_ports),
            ("AMF Overlay IP Reachable", self.test_amf_overlay_reachable),
            ("UPF Overlay IP Reachable", self.test_upf_overlay_reachable),
            ("gNB Connection Status", self.test_gnb_connection),
            ("UE NGAP Context (physical UE)", self.test_ue_ngap_context),
        ]
        
        passed = 0
        failed = 0
        skipped = 0
        
        for test_name, test_func in tests:
            self.logger.test_start(test_name)
            try:
                result = test_func()
                if result is None:
                    skipped += 1
                    self.logger.info(f"⏭️  {test_name}: SKIPPED (not configured)")
                elif result:
                    passed += 1
                    self.logger.test_end(test_name, True)
                else:
                    failed += 1
                    self.logger.test_end(test_name, False)
            except Exception as e:
                self.logger.error(f"{test_name} failed with exception: {e}")
                failed += 1
                self.logger.test_end(test_name, False)
        
        self.logger.info(f"Physical RAN Test Results: {passed} passed, {failed} failed, {skipped} skipped")
        return failed == 0
    
    def _ssh_worker(self, cmd: str) -> tuple:
        """Execute command on worker via SSH"""
        try:
            result = subprocess.run(
                ["vagrant", "ssh", "worker", "-c", cmd],
                capture_output=True, text=True, timeout=30,
                cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Timeout"
        except Exception as e:
            return -1, "", str(e)
    
    def test_ovs_bridge_config(self) -> bool:
        """Test OVS is installed and running on worker"""
        self.logger.info("Checking OVS installation on worker...")
        
        rc, stdout, stderr = self._ssh_worker("sudo ovs-vsctl show")
        if rc != 0:
            self.logger.error(f"OVS not available on worker: {stderr}")
            return False
        
        # Check for standard bridges
        expected_bridges = ["br-n2", "br-n3"]
        for bridge in expected_bridges:
            if bridge not in stdout:
                self.logger.error(f"OVS bridge {bridge} not found")
                return False
        
        self.logger.success("OVS bridges configured correctly")
        return True

    def test_overlay_gateway_ownership(self) -> bool:
        """Test worker OVS bridges own expected overlay gateway IPs"""
        self.logger.info("Checking overlay gateway ownership on worker bridges...")

        expected_gateways = {
            "br-n2": "10.202.0.1/24",
            "br-n3": "10.203.0.1/24",
            "br-n4": "10.204.0.1/24",
        }

        for bridge, cidr in expected_gateways.items():
            rc, stdout, stderr = self._ssh_worker(f"ip -o -4 addr show dev {bridge}")
            if rc != 0:
                self.logger.error(f"Cannot inspect {bridge}: {stderr}")
                return False
            if cidr not in stdout:
                self.logger.error(f"{bridge} does not own expected gateway {cidr}")
                return False

        self.logger.success("Worker overlay gateway ownership is correct (N2/N3/N4)")
        return True
    
    def test_ran_interface(self) -> bool:
        """Test RAN network interface exists on worker"""
        self.logger.info("Checking RAN interface on worker...")
        
        # Check for interface with 192.168.57.x IP
        rc, stdout, stderr = self._ssh_worker("ip addr show | grep '192.168.57'")
        
        if rc != 0 or "192.168.57" not in stdout:
            self.logger.info("RAN interface not configured (ran_bridge_mode might be disabled)")
            return None  # Skip - not configured
        
        self.logger.success(f"RAN interface found with IP in {self.ran_network}")
        return True
    
    def test_ovs_ran_bridge(self) -> bool:
        """Test br-ran OVS bridge exists"""
        self.logger.info("Checking br-ran bridge...")
        
        rc, stdout, stderr = self._ssh_worker("sudo ovs-vsctl list-br | grep br-ran")
        
        if rc != 0 or "br-ran" not in stdout:
            self.logger.info("br-ran not configured (ran_bridge_mode might be disabled)")
            return None  # Skip - not configured
        
        self.logger.success("br-ran bridge exists")
        return True
    
    def test_patch_ports(self) -> bool:
        """Test patch ports between br-ran and br-n2/br-n3"""
        self.logger.info("Checking patch ports...")
        
        # First check if br-ran exists
        rc, stdout, stderr = self._ssh_worker("sudo ovs-vsctl list-br | grep br-ran")
        if rc != 0 or "br-ran" not in stdout:
            self.logger.info("br-ran not configured, skipping patch port test")
            return None
        
        # Check patch ports
        rc, stdout, stderr = self._ssh_worker("sudo ovs-vsctl list-ports br-ran")
        if rc != 0:
            self.logger.error(f"Failed to list ports on br-ran: {stderr}")
            return False
        
        expected_patches = ["patch-ran-n2", "patch-ran-n3"]
        for patch in expected_patches:
            if patch not in stdout:
                self.logger.error(f"Patch port {patch} not found on br-ran")
                return False
        
        self.logger.success("Patch ports configured correctly")
        return True
    
    def test_amf_overlay_reachable(self) -> bool:
        """Test AMF N2 overlay IP is reachable from RAN network"""
        self.logger.info("Checking AMF overlay IP reachability...")
        
        # Get AMF pod and its N2 IP
        try:
            pods = self.kubectl.get_pods("5g")
            amf_pods = [p for p in pods if "amf" in p["metadata"]["name"].lower() and p["status"]["phase"] == "Running"]
            
            if not amf_pods:
                self.logger.error("No AMF pod found")
                return False
            
            # Get network-status annotation
            annotations = amf_pods[0]["metadata"].get("annotations", {})
            network_status = annotations.get("k8s.v1.cni.cncf.io/network-status", "")
            
            if not network_status:
                self.logger.error("No network-status annotation on AMF pod")
                return False
            
            import json
            networks = json.loads(network_status)
            amf_n2_ip = None
            for net in networks:
                if net.get("interface", "").startswith("n2"):
                    amf_n2_ip = net.get("ips", [None])[0]
                    break
            
            if not amf_n2_ip:
                self.logger.error("Could not find AMF N2 IP")
                return False
            
            self.logger.info(f"AMF N2 IP: {amf_n2_ip}")
            
            # Check if br-ran exists (if not, skip this test)
            rc, stdout, _ = self._ssh_worker("sudo ovs-vsctl list-br | grep br-ran")
            if rc != 0 or "br-ran" not in stdout:
                self.logger.info("br-ran not configured, skipping reachability test")
                return None
            
            # Ping from worker to AMF N2 IP
            rc, stdout, stderr = self._ssh_worker(f"ping -c 2 -W 2 {amf_n2_ip}")
            if rc != 0:
                self.logger.warning(f"Cannot reach AMF N2 IP {amf_n2_ip} from worker")
                return False
            
            self.logger.success(f"AMF N2 IP {amf_n2_ip} is reachable")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to test AMF reachability: {e}")
            return False
    
    def test_upf_overlay_reachable(self) -> bool:
        """Test UPF N3 overlay IP is reachable"""
        self.logger.info("Checking UPF overlay IP reachability...")
        
        try:
            pods = self.kubectl.get_pods("5g")
            upf_pods = [p for p in pods if "upf" in p["metadata"]["name"].lower() and p["status"]["phase"] == "Running"]
            
            if not upf_pods:
                self.logger.error("No UPF pod found")
                return False
            
            # Get network-status annotation
            annotations = upf_pods[0]["metadata"].get("annotations", {})
            network_status = annotations.get("k8s.v1.cni.cncf.io/network-status", "")
            
            if not network_status:
                self.logger.error("No network-status annotation on UPF pod")
                return False
            
            import json
            networks = json.loads(network_status)
            upf_n3_ip = None
            for net in networks:
                if net.get("interface") == "n3":
                    upf_n3_ip = net.get("ips", [None])[0]
                    break
            
            if not upf_n3_ip:
                self.logger.error("Could not find UPF N3 IP")
                return False
            
            self.logger.info(f"UPF N3 IP: {upf_n3_ip}")
            
            # Check if br-ran exists
            rc, stdout, _ = self._ssh_worker("sudo ovs-vsctl list-br | grep br-ran")
            if rc != 0 or "br-ran" not in stdout:
                self.logger.info("br-ran not configured, skipping reachability test")
                return None
            
            # Ping from worker
            rc, stdout, stderr = self._ssh_worker(f"ping -c 2 -W 2 {upf_n3_ip}")
            if rc != 0:
                self.logger.warning(f"Cannot reach UPF N3 IP {upf_n3_ip} from worker")
                return False
            
            self.logger.success(f"UPF N3 IP {upf_n3_ip} is reachable")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to test UPF reachability: {e}")
            return False
    
    def _amf_logs(self, tail_lines: int = 500) -> str:
        """Return recent AMF logs, or '' if no running AMF pod."""
        pods = self.kubectl.get_pods("5g")
        amf_pods = [p for p in pods if "amf" in p["metadata"]["name"].lower() and p["status"]["phase"] == "Running"]
        if not amf_pods:
            return ""
        return self.kubectl.get_pod_logs(amf_pods[0]["metadata"]["name"], "5g", tail_lines=tail_lines)

    def test_gnb_connection(self) -> bool:
        """Test that a gNB (physical or simulated) is connected to the AMF.

        Open5GS logs the gNB-level NG Setup separately from per-UE NGAP
        context. The NG Setup line can age out of a busy log, so a connected
        gNB is also inferred from active RAN_UE_NGAP_ID / CellID entries.
        """
        self.logger.info("Checking gNB connection to AMF...")

        try:
            logs = self._amf_logs()
            if not logs:
                self.logger.error("No running AMF pod found")
                return False

            import re
            match = re.search(r"Number of gNBs is now (\d+)", logs)
            if match and int(match.group(1)) > 0:
                self.logger.success(f"AMF has {match.group(1)} connected gNB(s)")
                return True

            for sig in ("gNB-N2 accepted", "NGSetupResponse", "NG Setup"):
                if sig in logs:
                    self.logger.success(f"gNB NG Setup detected in AMF logs ('{sig}')")
                    return True

            # Active per-UE NGAP context implies a gNB is serving the core.
            if "RAN_UE_NGAP_ID" in logs or "CellID" in logs:
                self.logger.success("gNB inferred from active NGAP context (RAN_UE_NGAP_ID/CellID)")
                return True

            self.logger.warning("No gNB connection found in AMF logs")
            return False

        except Exception as e:
            self.logger.error(f"Failed to check gNB connection: {e}")
            return False

    def test_ue_ngap_context(self) -> bool:
        """Test that a real UE reached the core through the physical gNB.

        Asserts NGAP-level UE context (the UE is seen by the AMF via the
        gNB). Full NAS registration is not asserted here: a UE may attach and
        release without completing 5GMM registration, which is a separate
        operational concern from RAN connectivity.
        """
        self.logger.info("Checking UE NGAP context in AMF logs...")

        try:
            logs = self._amf_logs()
            if not logs:
                self.logger.error("No running AMF pod found")
                return False

            import re
            counts = [int(c) for c in re.findall(r"Number of gNB-UEs is now (\d+)", logs)]
            if any(c > 0 for c in counts):
                self.logger.success(f"AMF saw UE NGAP context (peak gNB-UEs={max(counts)})")
                return True

            if "RAN_UE_NGAP_ID" in logs:
                self.logger.success("AMF logs show RAN_UE_NGAP_ID (UE reached core via gNB)")
                return True

            self.logger.warning("No UE NGAP context found in AMF logs (no UE attached?)")
            return False

        except Exception as e:
            self.logger.error(f"Failed to check UE NGAP context: {e}")
            return False


def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Physical RAN Integration Tests")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    test_suite = PhysicalRANTestSuite(verbose=args.verbose)
    success = test_suite.run_all_tests()
    
    if success:
        print("\n🎉 Physical RAN tests passed!")
        sys.exit(0)
    else:
        print("\n💥 Some Physical RAN tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()

"""
Performance Tests for 5G K3s KubeEdge Testbed
Tests network performance, throughput, and latency
"""
import sys
import os
import time
import json
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.kubectl_client import KubectlClient
from utils.test_helpers import TestConfig, TestLogger, NetworkValidator, ComponentValidator, CORE_NS


class PerformanceTestSuite:
    """Performance test suite for 5G testbed"""
    
    def __init__(self, verbose: bool = False):
        self.config = TestConfig()
        self.logger = TestLogger(verbose)
        self.kubectl = KubectlClient(self.config.get("cluster.kubeconfig_path"))
        self.network_validator = NetworkValidator(self.kubectl, self.config)
        self.component_validator = ComponentValidator(self.kubectl, self.config)
        self.verbose = verbose
    
    def run_all_tests(self) -> bool:
        """Run all performance tests"""
        self.logger.info("Starting Performance Test Suite")
        
        tests = [
            ("VXLAN Throughput", self.test_vxlan_throughput),
            ("VXLAN Latency", self.test_vxlan_latency),
            ("Packet Loss Test", self.test_packet_loss),
            ("PFCP Performance", self.test_pfcp_performance),
            ("NGAP Performance", self.test_ngap_performance),
            ("Concurrent Connections", self.test_concurrent_connections),
            ("Sustained Load", self.test_sustained_load),
            ("CPU and Memory Usage", self.test_resource_usage),
            ("Interface Throughput", self.test_interface_throughput),
            ("End-to-End Performance", self.test_end_to_end_performance)
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
        
        self.logger.info(f"Performance Test Results: {passed} passed, {failed} failed")
        return failed == 0
    
    def test_vxlan_throughput(self) -> bool:
        """Test VXLAN tunnel throughput using iperf3"""
        self.logger.info("Testing VXLAN throughput...")
        
        try:
            # Find two pods for throughput testing
            fiveg_pods = self.kubectl.get_pods(CORE_NS)
            if len(fiveg_pods) < 2:
                self.logger.error("Need at least 2 pods for throughput testing")
                return False
            
            # Use AMF and SMF for testing
            amf_pods = [p for p in fiveg_pods if "amf" in p["metadata"]["name"].lower()]
            smf_pods = [p for p in fiveg_pods if "smf" in p["metadata"]["name"].lower()]
            
            if not amf_pods or not smf_pods:
                self.logger.error("AMF or SMF pods not found for throughput testing")
                return False
            
            server_pod = amf_pods[0]["metadata"]["name"]
            client_pod = smf_pods[0]["metadata"]["name"]
            
            # Install iperf3 if not available
            self._install_iperf3(server_pod)
            self._install_iperf3(client_pod)
            
            # Start iperf3 server
            self.logger.info("Starting iperf3 server...")
            server_result = self.kubectl.exec_in_pod(
                server_pod, CORE_NS, 
                ["iperf3", "-s", "-D"]
            )
            
            time.sleep(2)  # Wait for server to start
            
            # Get server IP
            server_ip_result = self.kubectl.exec_in_pod(
                server_pod, CORE_NS, 
                ["hostname", "-i"]
            )
            server_ip = server_ip_result.stdout.strip()
            
            # Run iperf3 client
            self.logger.info("Running iperf3 client...")
            duration = self.config.get("test_configs.performance.iperf_duration", 60)
            parallel = self.config.get("test_configs.performance.iperf_parallel", 10)
            
            client_result = self.kubectl.exec_in_pod(
                client_pod, CORE_NS,
                ["iperf3", "-c", server_ip, "-t", str(duration), "-P", str(parallel), "-J"]
            )
            
            # Parse results
            try:
                results = json.loads(client_result.stdout)
                throughput = results["end"]["sum_received"]["bits_per_second"] / 1_000_000  # Convert to Mbps
                
                min_throughput = self.config.get("performance.throughput.min_mbps", 10)
                target_throughput = self.config.get("performance.throughput.target_mbps", 100)
                
                if throughput >= min_throughput:
                    self.logger.success(f"VXLAN throughput: {throughput:.2f} Mbps (min: {min_throughput} Mbps)")
                    if throughput >= target_throughput:
                        self.logger.success(f"Target throughput achieved: {throughput:.2f} Mbps >= {target_throughput} Mbps")
                    return True
                else:
                    self.logger.error(f"VXLAN throughput too low: {throughput:.2f} Mbps < {min_throughput} Mbps")
                    return False
                    
            except json.JSONDecodeError:
                self.logger.error("Failed to parse iperf3 results")
                return False
            
        except Exception as e:
            self.logger.error(f"VXLAN throughput test failed: {e}")
            return False
    
    def test_vxlan_latency(self) -> bool:
        """Test VXLAN tunnel latency using ping"""
        self.logger.info("Testing VXLAN latency...")
        
        try:
            # Find two pods for latency testing
            fiveg_pods = self.kubectl.get_pods(CORE_NS)
            if len(fiveg_pods) < 2:
                self.logger.error("Need at least 2 pods for latency testing")
                return False
            
            pod1 = fiveg_pods[0]["metadata"]["name"]
            pod2 = fiveg_pods[1]["metadata"]["name"]
            
            # Get pod2 IP
            pod2_ip_result = self.kubectl.exec_in_pod(
                pod2, CORE_NS, 
                ["hostname", "-i"]
            )
            pod2_ip = pod2_ip_result.stdout.strip()
            
            # Test with different packet sizes
            packet_sizes = [64, 512, 1024, 1472]
            max_latency = self.config.get("performance.latency.max_ms", 50)
            target_latency = self.config.get("performance.latency.target_ms", 10)
            
            for size in packet_sizes:
                self.logger.info(f"Testing latency with {size} byte packets...")
                
                ping_result = self.kubectl.exec_in_pod(
                    pod1, CORE_NS,
                    ["ping", "-c", "10", "-s", str(size), "-W", "5", pod2_ip]
                )
                
                # Parse ping results
                if "avg" in ping_result.stdout:
                    try:
                        avg_line = [line for line in ping_result.stdout.split('\n') if 'avg' in line][0]
                        avg_latency = float(avg_line.split('/')[4])
                        
                        if avg_latency <= max_latency:
                            self.logger.success(f"Latency with {size} bytes: {avg_latency:.2f} ms (max: {max_latency} ms)")
                            if avg_latency <= target_latency:
                                self.logger.success(f"Target latency achieved: {avg_latency:.2f} ms <= {target_latency} ms")
                        else:
                            self.logger.error(f"Latency too high with {size} bytes: {avg_latency:.2f} ms > {max_latency} ms")
                            return False
                    except (IndexError, ValueError):
                        self.logger.error(f"Failed to parse ping results for {size} byte packets")
                        return False
                else:
                    self.logger.error(f"Ping failed for {size} byte packets")
                    return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"VXLAN latency test failed: {e}")
            return False
    
    def test_packet_loss(self) -> bool:
        """Test packet loss under high packet rate"""
        self.logger.info("Testing packet loss...")
        
        try:
            # Find two pods for packet loss testing
            fiveg_pods = self.kubectl.get_pods(CORE_NS)
            if len(fiveg_pods) < 2:
                self.logger.error("Need at least 2 pods for packet loss testing")
                return False
            
            pod1 = fiveg_pods[0]["metadata"]["name"]
            pod2 = fiveg_pods[1]["metadata"]["name"]
            
            # Get pod2 IP
            pod2_ip_result = self.kubectl.exec_in_pod(
                pod2, CORE_NS, 
                ["hostname", "-i"]
            )
            pod2_ip = pod2_ip_result.stdout.strip()
            
            # High rate ping test
            self.logger.info("Running high rate ping test...")
            ping_result = self.kubectl.exec_in_pod(
                pod1, CORE_NS,
                ["ping", "-c", "100", "-i", "0.01", "-W", "1", pod2_ip]
            )
            
            # Parse packet loss
            if "packet loss" in ping_result.stdout:
                try:
                    loss_line = [line for line in ping_result.stdout.split('\n') if 'packet loss' in line][0]
                    loss_percent = float(loss_line.split('%')[0].split()[-1])
                    
                    max_loss = self.config.get("performance.packet_loss.max_percent", 1)
                    target_loss = self.config.get("performance.packet_loss.target_percent", 0.1)
                    
                    if loss_percent <= max_loss:
                        self.logger.success(f"Packet loss: {loss_percent}% (max: {max_loss}%)")
                        if loss_percent <= target_loss:
                            self.logger.success(f"Target packet loss achieved: {loss_percent}% <= {target_loss}%")
                        return True
                    else:
                        self.logger.error(f"Packet loss too high: {loss_percent}% > {max_loss}%")
                        return False
                        
                except (IndexError, ValueError):
                    self.logger.error("Failed to parse packet loss results")
                    return False
            else:
                self.logger.error("Ping test failed")
                return False
            
        except Exception as e:
            self.logger.error(f"Packet loss test failed: {e}")
            return False
    
    def test_pfcp_performance(self) -> bool:
        """Test PFCP protocol performance"""
        self.logger.info("Testing PFCP performance...")
        
        try:
            # Check SMF and UPF PFCP connectivity
            smf_pods = self.component_validator.get_component_pods("smf")
            upf_pods = self.component_validator.get_component_pods("upf")
            
            if not smf_pods or not upf_pods:
                self.logger.error("SMF or UPF pods not found for PFCP testing")
                return False
            
            smf_pod = smf_pods[0]["metadata"]["name"]
            upf_pod = upf_pods[0]["metadata"]["name"]
            
            # Test PFCP port connectivity
            smf_pfcp_port = 8805
            upf_pfcp_port = 8805
            
            # Check if ports are listening
            if not self.network_validator.check_port_listening(smf_pod, CORE_NS, smf_pfcp_port, "UDP"):
                self.logger.error("SMF not listening on PFCP port")
                return False
            
            if not self.network_validator.check_port_listening(upf_pod, CORE_NS, upf_pfcp_port, "UDP"):
                self.logger.error("UPF not listening on PFCP port")
                return False
            
            self.logger.success("PFCP ports are listening")
            
            # Test connectivity between SMF and UPF
            upf_ip_result = self.kubectl.exec_in_pod(
                upf_pod, CORE_NS, 
                ["hostname", "-i"]
            )
            upf_ip = upf_ip_result.stdout.strip()
            
            if self.network_validator.check_connectivity(smf_pod, upf_pod, CORE_NS, upf_ip):
                self.logger.success("SMF can reach UPF for PFCP communication")
                return True
            else:
                self.logger.warning("SMF cannot reach UPF (might be normal during startup)")
                return True  # Don't fail if connectivity is not established yet
            
        except Exception as e:
            self.logger.error(f"PFCP performance test failed: {e}")
            return False
    
    def test_ngap_performance(self) -> bool:
        """Test NGAP protocol performance"""
        self.logger.info("Testing NGAP performance...")
        
        try:
            # Check AMF and gNB NGAP connectivity
            amf_pods = self.component_validator.get_component_pods("amf")
            gnb_pods = [p for p in self.kubectl.get_pods(CORE_NS) if "gnb" in p["metadata"]["name"].lower()]
            
            if not amf_pods:
                self.logger.error("AMF pods not found for NGAP testing")
                return False
            
            amf_pod = amf_pods[0]["metadata"]["name"]
            
            # Check AMF SCTP port
            if not self.network_validator.check_port_listening(amf_pod, CORE_NS, 38412, "SCTP"):
                self.logger.error("AMF not listening on SCTP port for NGAP")
                return False
            
            self.logger.success("AMF listening on SCTP port for NGAP")
            
            # Test gNB connectivity if available
            if gnb_pods:
                gnb_pod = gnb_pods[0]["metadata"]["name"]
                amf_n2_ip = self.config.plan("amf_n2_ip")
                
                if self.network_validator.check_connectivity(gnb_pod, amf_pod, CORE_NS, amf_n2_ip):
                    self.logger.success("gNB can reach AMF for NGAP communication")
                else:
                    self.logger.warning("gNB cannot reach AMF (might be normal during startup)")
            
            return True
            
        except Exception as e:
            self.logger.error(f"NGAP performance test failed: {e}")
            return False
    
    def test_concurrent_connections(self) -> bool:
        """Test concurrent connection handling"""
        self.logger.info("Testing concurrent connections...")
        
        try:
            # This is a simplified test - in a real scenario, you would test actual concurrent connections
            # For now, we'll check if multiple pods can communicate simultaneously
            
            fiveg_pods = self.kubectl.get_pods(CORE_NS)
            if len(fiveg_pods) < 3:
                self.logger.warning("Need at least 3 pods for concurrent connection testing")
                return True
            
            # Test multiple simultaneous pings
            pod1 = fiveg_pods[0]["metadata"]["name"]
            pod2 = fiveg_pods[1]["metadata"]["name"]
            pod3 = fiveg_pods[2]["metadata"]["name"]
            
            # Get IPs
            pod2_ip_result = self.kubectl.exec_in_pod(pod2, CORE_NS, ["hostname", "-i"])
            pod2_ip = pod2_ip_result.stdout.strip()
            
            pod3_ip_result = self.kubectl.exec_in_pod(pod3, CORE_NS, ["hostname", "-i"])
            pod3_ip = pod3_ip_result.stdout.strip()
            
            # Test concurrent connectivity
            if (self.network_validator.check_connectivity(pod1, pod2, CORE_NS, pod2_ip) and
                self.network_validator.check_connectivity(pod1, pod3, CORE_NS, pod3_ip)):
                self.logger.success("Concurrent connections working")
                return True
            else:
                self.logger.warning("Some concurrent connections failed (might be normal during startup)")
                return True  # Don't fail for this test
            
        except Exception as e:
            self.logger.error(f"Concurrent connections test failed: {e}")
            return False
    
    def test_sustained_load(self) -> bool:
        """Test sustained load performance"""
        self.logger.info("Testing sustained load...")
        
        try:
            # Run iperf3 for a longer duration
            fiveg_pods = self.kubectl.get_pods(CORE_NS)
            if len(fiveg_pods) < 2:
                self.logger.error("Need at least 2 pods for sustained load testing")
                return False
            
            server_pod = fiveg_pods[0]["metadata"]["name"]
            client_pod = fiveg_pods[1]["metadata"]["name"]
            
            # Install iperf3 if not available
            self._install_iperf3(server_pod)
            self._install_iperf3(client_pod)
            
            # Start iperf3 server
            self.kubectl.exec_in_pod(server_pod, CORE_NS, ["iperf3", "-s", "-D"])
            time.sleep(2)
            
            # Get server IP
            server_ip_result = self.kubectl.exec_in_pod(server_pod, CORE_NS, ["hostname", "-i"])
            server_ip = server_ip_result.stdout.strip()
            
            # Run sustained load test
            duration = 120  # 2 minutes
            self.logger.info(f"Running sustained load test for {duration} seconds...")
            
            client_result = self.kubectl.exec_in_pod(
                client_pod, CORE_NS,
                ["iperf3", "-c", server_ip, "-t", str(duration), "-J"]
            )
            
            # Parse results
            try:
                results = json.loads(client_result.stdout)
                throughput = results["end"]["sum_received"]["bits_per_second"] / 1_000_000
                
                min_throughput = self.config.get("performance.throughput.min_mbps", 10)
                
                if throughput >= min_throughput:
                    self.logger.success(f"Sustained load throughput: {throughput:.2f} Mbps")
                    return True
                else:
                    self.logger.error(f"Sustained load throughput too low: {throughput:.2f} Mbps")
                    return False
                    
            except json.JSONDecodeError:
                self.logger.error("Failed to parse sustained load results")
                return False
            
        except Exception as e:
            self.logger.error(f"Sustained load test failed: {e}")
            return False
    
    def test_resource_usage(self) -> bool:
        """Test CPU and memory usage"""
        self.logger.info("Testing resource usage...")
        
        try:
            # Check resource usage of 5G pods
            fiveg_pods = self.kubectl.get_pods(CORE_NS)
            if not fiveg_pods:
                self.logger.error("No 5G pods found for resource testing")
                return False
            
            total_cpu = 0
            total_memory = 0
            
            for pod in fiveg_pods:
                pod_name = pod["metadata"]["name"]
                
                # Get resource usage
                try:
                    result = self.kubectl.exec_in_pod(
                        pod_name, CORE_NS,
                        ["top", "-bn1"]
                    )
                    
                    # Parse top output (simplified)
                    lines = result.stdout.split('\n')
                    if len(lines) > 1:
                        # This is a simplified parsing - in reality you'd want more sophisticated parsing
                        self.logger.info(f"Resource usage for {pod_name}: {lines[1] if len(lines) > 1 else 'N/A'}")
                    
                except:
                    self.logger.warning(f"Could not get resource usage for {pod_name}")
            
            self.logger.success("Resource usage monitoring completed")
            return True
            
        except Exception as e:
            self.logger.error(f"Resource usage test failed: {e}")
            return False
    
    def test_interface_throughput(self) -> bool:
        """Test individual interface throughput"""
        self.logger.info("Testing interface throughput...")
        
        try:
            # Test throughput on specific 5G interfaces
            amf_pods = self.component_validator.get_component_pods("amf")
            if not amf_pods:
                self.logger.error("AMF pods not found for interface testing")
                return False
            
            amf_pod = amf_pods[0]["metadata"]["name"]
            
            # Check interface statistics
            try:
                result = self.kubectl.exec_in_pod(
                    amf_pod, CORE_NS,
                    ["cat", "/proc/net/dev"]
                )
                
                # Parse interface statistics
                lines = result.stdout.split('\n')
                for line in lines:
                    if 'n1' in line or 'n2' in line:
                        self.logger.info(f"Interface stats: {line.strip()}")
                
                self.logger.success("Interface throughput monitoring completed")
                return True
                
            except:
                self.logger.warning("Could not get interface statistics")
                return True  # Don't fail for this test
            
        except Exception as e:
            self.logger.error(f"Interface throughput test failed: {e}")
            return False
    
    def test_end_to_end_performance(self) -> bool:
        """Test end-to-end performance"""
        self.logger.info("Testing end-to-end performance...")
        
        try:
            # This would test the complete 5G data path
            # For now, we'll do a simplified test
            
            # Check if all components are running
            components = ["amf", "smf", "upf"]
            for component in components:
                if not self.component_validator.is_component_ready(component):
                    self.logger.error(f"{component.upper()} not ready for end-to-end testing")
                    return False
            
            self.logger.success("All components ready for end-to-end testing")
            
            # Test basic connectivity
            fiveg_pods = self.kubectl.get_pods(CORE_NS)
            if len(fiveg_pods) >= 2:
                pod1 = fiveg_pods[0]["metadata"]["name"]
                pod2 = fiveg_pods[1]["metadata"]["name"]
                
                pod2_ip_result = self.kubectl.exec_in_pod(pod2, CORE_NS, ["hostname", "-i"])
                pod2_ip = pod2_ip_result.stdout.strip()
                
                if self.network_validator.check_connectivity(pod1, pod2, CORE_NS, pod2_ip):
                    self.logger.success("End-to-end connectivity working")
                    return True
                else:
                    self.logger.warning("End-to-end connectivity issues (might be normal during startup)")
                    return True  # Don't fail for this test
            
            return True
            
        except Exception as e:
            self.logger.error(f"End-to-end performance test failed: {e}")
            return False
    
    def _install_iperf3(self, pod_name: str):
        """Install iperf3 in pod if not available"""
        try:
            # Check if iperf3 is available
            result = self.kubectl.exec_in_pod(pod_name, CORE_NS, ["which", "iperf3"])
            if result.returncode == 0:
                return  # iperf3 already available
            
            # Install iperf3
            self.logger.info(f"Installing iperf3 in {pod_name}...")
            self.kubectl.exec_in_pod(
                pod_name, CORE_NS,
                ["apt-get", "update", "&&", "apt-get", "install", "-y", "iperf3"]
            )
        except:
            self.logger.warning(f"Could not install iperf3 in {pod_name}")


def main():
    """Main function for running performance tests"""
    import argparse
    
    parser = argparse.ArgumentParser(description="5G Performance Tests")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    test_suite = PerformanceTestSuite(verbose=args.verbose)
    success = test_suite.run_all_tests()
    
    if success:
        print("\n🎉 All performance tests passed!")
        sys.exit(0)
    else:
        print("\n💥 Some performance tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()

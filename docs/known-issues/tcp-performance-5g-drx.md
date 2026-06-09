# TCP Performance Over 5G Radio: DRX and Endpoint Colocation

## DRX-induced TCP slow-start

The gNB schedules UL grants periodically rather than continuously. When DRX (Discontinuous Reception) is active, the UE enters sleep cycles of roughly 200-300 ms between active periods. TCP ACKs queue in the UE's transmit buffer during sleep and are released in a batch on the next UL grant. The server interprets these ACK gaps as congestion and backs off, causing TCP CUBIC to require 5-6 seconds to converge to the available radio bandwidth.

This is expected radio scheduler behavior and does not indicate a core or overlay misconfiguration.

**Consequence for testing:** iperf3 tests shorter than 20 seconds report artificially low throughput because most of the measurement window is in the slow-start transient. Use `-t 30` as the minimum for TCP throughput measurements.

**Measured baselines (worker at 10.207.0.1, single stream, Teltonika 5G modem as UE):**

| Direction | 30s average | Steady-state burst | Convergence time |
|-----------|-------------|-------------------|-----------------|
| DL (worker → UE) | 205 Mbits/sec | 230-262 Mbits/sec | ~5 s |
| UL (UE → worker) | 56 Mbits/sec | ~94 Mbits/sec | ~6 s |

UDP is unaffected by DRX: 100 Mbits/sec DL, 0% loss, 0.1 ms jitter (verified at NUC endpoint and worker).

## TCP server colocation requirement

TCP DL performance degrades to below 1 Mbits/sec when the server is a host outside the K3s worker node (for example, the NUC host at 192.168.56.1). UDP to the same host delivers 100 Mbits/sec without packet loss.

The root cause is RTT jitter introduced by the path from the server through VirtualBox virtual networking back into the OVS overlay. ACKs from the UE traverse GTP-U decapsulation, UPF SNAT, and the OVS bridge before reaching the worker, and then must make an additional hop through VirtualBox virtual NICs to reach the external host. The combined DRX-induced ACK batching and VirtualBox scheduling jitter pushes observed RTT above the TCP retransmit timeout. CWND collapses and never recovers within the test duration. Switching congestion control algorithms (BBR, CUBIC) does not resolve the issue.

The worker node is collocated with the OVS bridge and the UPF pod. ACKs arrive at the server without the extra VirtualBox hop, so the RTT is stable and CWND converges normally.

**Rule:** TCP iperf servers for 5G path characterization must be bound to a worker interface in the N6 overlay (10.207.0.1 for DNN internet, 10.208.0.1 for DNN mec). External hosts outside the worker node are not suitable as TCP endpoints for throughput testing over this radio path.

## Double NAT and TCP DL from the test laptop

The Teltonika modem applies SNAT (laptop 192.168.2.116 → modem PDU session IP 10.45.x.x) before the UPF applies its own SNAT (10.45.x.x → 10.207.0.100). When the TCP server is external to the worker (for example, the NUC), the DL ACK path traverses both conntrack layers and VirtualBox networking, producing the same CWND collapse described above.

Tests from the laptop to 10.45.0.1 (UPF ogstun) and 10.207.0.1 (worker br-n6c) are not affected because those endpoints are collocated with the GTP-U conntrack state.

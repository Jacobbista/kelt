# Latency segments — decomposing the RTT budget

The network campaigns (`run.sh throughput`, `run.sh rtt`) measure ONE segment end-to-end: UE → the
measurement server on N6m (`apps_measurement_server_n6m_ip`). This file schematises the path into isolable segments so the RTT
budget can be attributed — the transport equivalent of the dashboard's sniffer
capture points, and the way to show the impact of **virtualisation and
orchestration** on the intra-cluster legs.

Two families, two vantage points.

## A. 5G transport (from the UE)

| Segment | Target (from the UE) | Isolates | Note |
|---|---|---|---|
| **Uu + N3 + UPF + N6m** | the measurement server in `mec` on n6m | the path the edge application uses (headline) | reached from the UE's `internet` session, routed by the UPF without NAT |

The radio leg cannot be isolated by a ping target. The gNB's RAN address
`192.168.6.101` is not one hop away: the packet still goes over GTP-U to the
UPF, which sends it back to the gNB over N3, and the reply comes back the same
way.

It is split instead by a capture on `br-ran` on the worker (where the gNB's
GTP-U enters from the wire) during the rtt idle runs (`network/rtt.py`): `tcpdump -tt -v udp port 2152`
decodes the ICMP inside GTP-U, so each echo request is paired with its reply by
id and seq.

- **core** = reply leaves `br-ran` − request enters `br-ran` (the UPF, N6m, the server, and back);
- **access** = RTT at the probe − core (probe PC, modem, Uu, gNB processing,
  cable to the worker).

Each difference is taken on one clock, so no clock sync is needed. Uu and the
gNB's own processing stay together: the gNB is a black box.

## B. Virtualisation & orchestration (from netshoot pods, via `latency-segments.sh`)

These quantify the overhead the container platform adds on top of the raw host
network. Each row is measured from an ephemeral netshoot pod so it is
reproducible and self-cleaning.

| Segment | How | Isolates |
|---|---|---|
| **host baseline** | node → node (InternalIP) | the underlying network floor |
| **pod → pod, same node** | netshoot(worker) → podIP(worker) | container CNI only |
| **pod → pod, cross node** | netshoot(master) → podIP(worker) | CNI **+ VXLAN overlay** (overlay cost = this − host baseline) |
| **pod → ClusterIP** | netshoot → service ClusterIP (TCP connect) | kube-proxy / iptables (svc cost = this − podIP) |
| **pod → NodePort / front-door** | netshoot → nodeIP:nodePort (TCP connect) | ingress hop (nodeport cost = this − ClusterIP) |
| **pipeline legs** | netshoot → gateway/engine/adapter podIPs | the network legs the CAMARA request crosses |
| **n6m Multus leg** *(opt)* | netshoot → an n6m IP (10.208.0.x) | the secondary Multus data-network path |

Note on scale: intra-cluster legs are typically sub-millisecond. They matter for
the *virtualisation-overhead* story (how much K8s/overlay/kube-proxy adds), not
as a large share of a CAMARA request — the application stage split (northbound
log line) and the external vendor round trip dominate that. Report both, and say
which is which.

Owner: experiments/README.md. The application-stage split of a CAMARA request
(gateway/engine/adapter processing) is NOT here — it is in-process time only the
services can emit; see experiments/exposure/README.md.

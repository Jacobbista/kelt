# Latency segments — decomposing the RTT budget

The headline campaigns (C1–C3) measure ONE segment end-to-end: UE → UPF anchor
`10.45.0.1`. This file schematises the path into isolable segments so the RTT
budget can be attributed — the transport equivalent of the dashboard's sniffer
capture points, and the way to show the impact of **virtualisation and
orchestration** on the intra-cluster legs.

Two families, two vantage points.

## A. 5G transport (from the UE netns, via the probe)

Reuse `run-campaign.sh C2_latency_idle` with a target override
(`KELT_TARGET=<ip>`); the probe accepts any reachable IP. The difference
between the two targets below is the N6m leg to the application.

| Segment | Target (from UE netns) | Isolates | Note |
|---|---|---|---|
| **Uu + N3 + UPF** | UPF anchor `10.45.0.1` | radio + backhaul + UPF (headline) | this is C2 as-is; the UPF itself answers, so N6 is not crossed |
| **Uu + N3 + UPF + N6m** | a server pod in `mec` on n6m | the path the edge application uses | UE on DNN `mec`; needs an iperf3/ping server there |

The radio leg cannot be isolated by a ping target. The gNB's RAN address
`192.168.6.101` is not one hop away: the packet still goes over GTP-U to the
UPF, which sends it back to the gNB over N3, and the reply comes back the same
way. That path is longer than the one to the UPF anchor.

It is split instead by a capture on `br-ran` on the worker (where the gNB's
GTP-U enters from the wire) during the C2 ping: `tcpdump -tt -v udp port 2152`
decodes the ICMP inside GTP-U, so each echo request is paired with its reply by
id and seq.

- **core** = reply leaves `br-ran` − request enters `br-ran` (N3, UPF, back);
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

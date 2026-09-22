# VirtualBox host-only adapter: multi-segment TSO frames stall host-to-cluster TCP

## Symptom

From the testbed host, any TCP request to a NodePort whose first burst is
larger than one segment takes about 200 ms longer than it should. A CAMARA
retrieve with a bearer token (headers around 1.5 KB) answers in ~230 ms while
the gateway itself spends ~24 ms on it; a `GET /health` with no token answers
in 2 ms. The same request from inside a VM or a pod shows no delay.

## Cause

The pod behind the NodePort sits on the flannel VXLAN (MTU 1450) and
advertises MSS 1410 in its SYN-ACK. The host's TCP stack honours it and
segments at 1398 bytes (1410 minus 12 of timestamp options), but the
host-only adapter (`vboxnet0`) advertises TCP segmentation offload, so the
kernel hands the driver one super-frame holding several segments and expects
the "hardware" to split it. The VirtualBox host-only driver does not split;
the VM receives the whole frame on its host-only NIC, cannot forward it over
the 1450-byte VXLAN, and drops it without an ICMP. The host retransmits after
its 200 ms retransmission timeout, and retransmissions bypass the offload, so
the second attempt is correctly sized and goes through.

Captured on the master (`tcpdump -ni enp0s8`), request with ~1.5 KB of headers:

```
S.   mss 1410                               the pod's MSS
P.   seq 1:1498    length 1497              host sends one super-frame
P.   seq 1399:1498 length 99                second segment, delivered on its own
.    ack 1, sack {1399:1498}                 VM saw only the tail
(+200 ms)
.    seq 1:1399    length 1398              retransmission, correctly sized
```

`tx-tcp-segmentation` is reported `fixed` on `vboxnet`, so it cannot be
switched off with `ethtool -K`.

## Workaround

Limit the host-only adapter to one segment per frame, so the kernel never
builds a super-frame for it:

```bash
sudo ip link set dev vboxnet0 gso_max_segs 1
sudo ethtool -K vboxnet0 gso off
```

Verified on the host: the padded request drops from 210 ms to 2 ms, and so
does an authenticated CAMARA call. The setting lives on the interface and is
lost when VirtualBox recreates it or the host reboots, so the Vagrantfile
re-applies it in an `after :up/:reload/:resume` trigger on the master
(host-side, idempotent, needs `sudo`; when it cannot get it, it prints the two
commands and continues, because nothing in the deployment depends on this).
The interface is found by the host address of the private network, never by
name.

## Scope

Only traffic that originates on the host and enters the VMs through the
host-only adapter: `kubectl` through the master, NodePort calls, the
dashboard's dev frontend, and the measurement scripts in `experiments/`. It
does not touch traffic inside the cluster, between VMs, from a UE over the
femtocell, or through the external tunnel. Throughput measurements over the
radio are unaffected; latency measurements taken from the host are not, which
is why `experiments/run.sh E1` reports the gateway's own per-hop times and
treats the client-side number as informative only.

Related: [../architecture/network-topology.md](../architecture/network-topology.md)
(MTU sizing), [tcp-performance-5g-drx.md](tcp-performance-5g-drx.md)
(the other TCP-on-the-testbed effect, on the radio path).

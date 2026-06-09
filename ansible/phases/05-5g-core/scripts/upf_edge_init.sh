#!/bin/bash
set -e

echo "[UPF-Edge][init] Starting UPF-Edge initialization..."

# Wait for N3 and N6 interfaces
echo "[UPF-Edge][init] Waiting for N3 and N6 interfaces..."
while ! ip addr show n3 | grep -q "inet" || ! ip addr show n6 | grep -q "inet"; do
    sleep 1
done

# Ensure log directory exists
mkdir -p /var/log/open5gs

# Configure TUN interface and routing (idempotent)
if ! ip link show ogstun >/dev/null 2>&1; then
  ip tuntap add name ogstun mode tun
fi
if ! ip addr show dev ogstun | grep -q "10.46.0.1/16"; then
  ip addr add 10.46.0.1/16 dev ogstun || true
fi
# MTU 1400 on ogstun: overlay n3/n6 are 1450, GTP-U adds ~40 B of header,
# so the UE-side IP payload must stay <= 1410; 1400 gives safety margin.
# See docs/architecture/network-topology.md (MTU sizing and GTP-U encapsulation)
ip link set dev ogstun mtu 1400
ip link set ogstun up || true
iptables -t nat -C POSTROUTING -s 10.46.0.1/16 ! -o ogstun -j MASQUERADE 2>/dev/null || \
  iptables -t nat -A POSTROUTING -s 10.46.0.1/16 ! -o ogstun -j MASQUERADE

# TCP MSS clamping for UE traffic: forces remote peers to use
# MSS 1360 (= 1400 MTU - 20 IP - 20 TCP) so TCP flows survive GTP-U
# encapsulation without fragmentation, regardless of UE-side MTU.
# See docs/architecture/network-topology.md (MTU sizing and GTP-U encapsulation)
iptables -t mangle -C FORWARD -p tcp --tcp-flags SYN,RST SYN -o ogstun -j TCPMSS --set-mss 1360 2>/dev/null || \
  iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -o ogstun -j TCPMSS --set-mss 1360
iptables -t mangle -C FORWARD -p tcp --tcp-flags SYN,RST SYN -i ogstun -j TCPMSS --set-mss 1360 2>/dev/null || \
  iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -i ogstun -j TCPMSS --set-mss 1360

# iperf3 server is launched via lifecycle.postStart on the main UPF container
# (see roles/nf_deployments/defaults/main.yml). InitContainers terminate child
# processes on exit, so launching iperf3 here would not survive.

# Configure sysctls
sysctl -w net.ipv4.ip_forward=1
for i in all n3 n6; do sysctl -w net.ipv4.conf.$i.rp_filter=0; done

# Configure policy routing (idempotent)
ip rule show | grep -q "iif n3 lookup 100" || ip rule add iif n3 lookup 100
ip route show table 100 | grep -q "default via 10.203.0.1 dev n3" || ip route add default via 10.203.0.1 dev n3 table 100
ip rule show | grep -q "iif n6 lookup 200" || ip rule add iif n6 lookup 200
ip route show table 200 | grep -q "default via 10.206.0.1 dev n6" || ip route add default via 10.206.0.1 dev n6 table 200

echo "[UPF-Edge][init] Network setup complete."

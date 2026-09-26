#!/bin/bash
set -e

echo "[UPF-Edge][init] Starting UPF-Edge initialization..."

# Addresses and MTU come from the 5G network plan in all.yml via the pod env
# (roles/nf_deployments/defaults/main.yml). No fallbacks: a missing value is a deploy bug.
for v in UE_MEC_SUBNET UE_MEC_GATEWAY UE_MTU N3_GATEWAY N6_GATEWAY; do
  eval ": \"\${$v:?$v must be set in the pod env}\""
done
UE_MSS=$((UE_MTU - 40))
UE_MEC_CIDR="${UE_MEC_GATEWAY}/${UE_MEC_SUBNET#*/}"

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
if ! ip addr show dev ogstun | grep -q "$UE_MEC_CIDR"; then
  ip addr add "$UE_MEC_CIDR" dev ogstun || true
fi
# UE-side MTU: GTP-U adds ~40 B over the overlay MTU.
# See docs/architecture/network-topology.md (MTU sizing and GTP-U encapsulation)
ip link set dev ogstun mtu "$UE_MTU"
ip link set ogstun up || true
iptables -t nat -C POSTROUTING -s "$UE_MEC_SUBNET" ! -o ogstun -j MASQUERADE 2>/dev/null || \
  iptables -t nat -A POSTROUTING -s "$UE_MEC_SUBNET" ! -o ogstun -j MASQUERADE

# TCP MSS clamping for UE traffic: forces remote peers to use
# MSS = UE MTU - 20 IP - 20 TCP so TCP flows survive GTP-U
# encapsulation without fragmentation, regardless of UE-side MTU.
# See docs/architecture/network-topology.md (MTU sizing and GTP-U encapsulation)
iptables -t mangle -C FORWARD -p tcp --tcp-flags SYN,RST SYN -o ogstun -j TCPMSS --set-mss "$UE_MSS" 2>/dev/null || \
  iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -o ogstun -j TCPMSS --set-mss "$UE_MSS"
iptables -t mangle -C FORWARD -p tcp --tcp-flags SYN,RST SYN -i ogstun -j TCPMSS --set-mss "$UE_MSS" 2>/dev/null || \
  iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -i ogstun -j TCPMSS --set-mss "$UE_MSS"

# iperf3 server is launched via lifecycle.postStart on the main UPF container
# (see roles/nf_deployments/defaults/main.yml). InitContainers terminate child
# processes on exit, so launching iperf3 here would not survive.

# Configure sysctls
sysctl -w net.ipv4.ip_forward=1
for i in all n3 n6; do sysctl -w net.ipv4.conf.$i.rp_filter=0; done

# Configure policy routing (idempotent)
ip rule show | grep -q "iif n3 lookup 100" || ip rule add iif n3 lookup 100
ip route replace default via "$N3_GATEWAY" dev n3 table 100
ip rule show | grep -q "iif n6 lookup 200" || ip rule add iif n6 lookup 200
ip route replace default via "$N6_GATEWAY" dev n6 table 200

echo "[UPF-Edge][init] Network setup complete."

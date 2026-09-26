#!/bin/bash
set -e

echo "[UPF][init] Starting UPF initialization..."

# Addresses and MTU come from the 5G network plan in all.yml via the pod env
# (roles/nf_deployments/defaults/main.yml). No fallbacks: a missing value is a deploy bug.
for v in UE_INTERNET_SUBNET UE_INTERNET_GATEWAY UE_MEC_SUBNET UE_MEC_GATEWAY UE_MTU \
         N3_GATEWAY N6_GATEWAY N6M_SUBNET; do
  eval ": \"\${$v:?$v must be set in the pod env}\""
done
UE_MSS=$((UE_MTU - 40))
prefix_len() { echo "${1#*/}"; }

# Wait for N3, N6, and N6m interfaces
echo "[UPF][init] Waiting for N3 and N6 interfaces..."
while ! ip addr show n3 | grep -q "inet" || ! ip addr show n6 | grep -q "inet"; do
    sleep 1
done
echo "[UPF][init] Waiting for N6m (MEC) interface..."
while ! ip addr show n6m 2>/dev/null | grep -q "inet"; do
    sleep 1
done

# Ensure log directory exists
mkdir -p /var/log/open5gs

# Configure TUN interface and routing (idempotent)
if ! ip link show ogstun >/dev/null 2>&1; then
  ip tuntap add name ogstun mode tun
fi
if ! ip addr show dev ogstun | grep -q "${UE_INTERNET_GATEWAY}/$(prefix_len "$UE_INTERNET_SUBNET")"; then
  ip addr add "${UE_INTERNET_GATEWAY}/$(prefix_len "$UE_INTERNET_SUBNET")" dev ogstun || true
fi
# UE-side MTU: GTP-U adds ~40 B over the overlay MTU.
# See docs/architecture/network-topology.md (MTU sizing and GTP-U encapsulation)
ip link set dev ogstun mtu "$UE_MTU"
ip link set ogstun up || true
# Internet breakout is NATed on N6c only. Traffic to the MEC apps on N6m keeps
# the UE address: the apps route the UE pools back through this UPF.
iptables -t nat -C POSTROUTING -s "$UE_INTERNET_SUBNET" -o n6 -j MASQUERADE 2>/dev/null || \
  iptables -t nat -A POSTROUTING -s "$UE_INTERNET_SUBNET" -o n6 -j MASQUERADE

# Configure TUN interface for the MEC DNN (ogstun2)
if ! ip link show ogstun2 >/dev/null 2>&1; then
  ip tuntap add name ogstun2 mode tun
fi
if ! ip addr show dev ogstun2 | grep -q "${UE_MEC_GATEWAY}/$(prefix_len "$UE_MEC_SUBNET")"; then
  ip addr add "${UE_MEC_GATEWAY}/$(prefix_len "$UE_MEC_SUBNET")" dev ogstun2 || true
fi
# Same MTU rationale as ogstun: GTP-U over the N3 overlay (VXLAN only adds
# overhead when edge is enabled; GTP-U is the binding constraint either way).
ip link set dev ogstun2 mtu "$UE_MTU"
ip link set ogstun2 up || true

# TCP MSS clamping for UE traffic on both DNNs: forces remote peers to use
# MSS = UE MTU - 20 IP - 20 TCP so TCP flows survive GTP-U
# encapsulation without fragmentation, regardless of UE-side MTU.
# See docs/architecture/network-topology.md (MTU sizing and GTP-U encapsulation)
for IF in ogstun ogstun2; do
  iptables -t mangle -C FORWARD -p tcp --tcp-flags SYN,RST SYN -o "$IF" -j TCPMSS --set-mss "$UE_MSS" 2>/dev/null || \
    iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -o "$IF" -j TCPMSS --set-mss "$UE_MSS"
  iptables -t mangle -C FORWARD -p tcp --tcp-flags SYN,RST SYN -i "$IF" -j TCPMSS --set-mss "$UE_MSS" 2>/dev/null || \
    iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -i "$IF" -j TCPMSS --set-mss "$UE_MSS"
done

# The MEC DNN is a closed data network: its sessions reach the N6m apps directly
# and nothing else (no route through the worker, no internet).
# See docs/architecture/5g-interfaces.md#data-networks
ip rule show | grep -q "iif ogstun2 lookup 300" || ip rule add iif ogstun2 lookup 300 pref 20
ip route replace "$N6M_SUBNET" dev n6m table 300
ip route replace unreachable default table 300

# iperf3 server is launched via lifecycle.postStart on the main UPF container
# (see roles/nf_deployments/defaults/main.yml). InitContainers terminate child
# processes on exit, so launching iperf3 here would not survive.

# Configure sysctls
sysctl -w net.ipv4.ip_forward=1
for i in all n3 n6 n6m; do sysctl -w net.ipv4.conf.$i.rp_filter=0; done

# Configure policy routing (idempotent)
# Keep N3 symmetric policy routing for GTP-U return traffic.
ip rule show | grep -q "iif n3 lookup 100" || ip rule add iif n3 lookup 100 pref 30
ip route replace default via "$N3_GATEWAY" dev n3 table 100

# Remove legacy rule that can misroute UE-destined return packets into N6.
ip rule del iif n6 lookup 200 2>/dev/null || true
ip route replace default via "$N6_GATEWAY" dev n6 table 200

# Physical RAN return route: carried by the n3 interface itself (NAD
# 5g/n3-upf-static, phase 04), so it exists before this script runs.

# --- Redirect decapsulated (ogstun) traffic to the Data Network (N6) ---
# Overrides the K3s (eth0) default gateway to prevent leaks onto the management network
echo "[UPF][init] Redirecting default route to N6 Data Network interface..."
ip route replace default via "$N6_GATEWAY" dev n6
# --------------------------------------------------------------------------------

echo "[UPF][init] Network setup complete."

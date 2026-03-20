#!/bin/bash
set -e

echo "[UPF][init] Starting UPF initialization..."

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
mkdir -p /open5gs/install/var/log/open5gs

# Configure TUN interface and routing (idempotent)
if ! ip link show ogstun >/dev/null 2>&1; then
  ip tuntap add name ogstun mode tun
fi
if ! ip addr show dev ogstun | grep -q "10.45.0.1/16"; then
  ip addr add 10.45.0.1/16 dev ogstun || true
fi
ip link set ogstun up || true
iptables -t nat -C POSTROUTING -s 10.45.0.1/16 ! -o ogstun -j MASQUERADE 2>/dev/null || \
  iptables -t nat -A POSTROUTING -s 10.45.0.1/16 ! -o ogstun -j MASQUERADE

# Configure TUN interface for MEC DNN (ogstun2 → 10.46.0.0/16)
if ! ip link show ogstun2 >/dev/null 2>&1; then
  ip tuntap add name ogstun2 mode tun
fi
if ! ip addr show dev ogstun2 | grep -q "10.46.0.1/16"; then
  ip addr add 10.46.0.1/16 dev ogstun2 || true
fi
ip link set ogstun2 up || true

# Route MEC UE traffic (10.46.0.0/16) to the N6m interface (MEC services network)
ip rule show | grep -q "iif ogstun2 lookup 300" || ip rule add iif ogstun2 lookup 300 pref 20
ip route replace default via 10.208.0.1 dev n6m table 300

# Start iperf3 server (output to separate logfile to keep pod logs clean)
iperf3 -B 10.45.0.1 -s -fm -i 0.1 --logfile /var/log/iperf3-server.log &

# Configure sysctls
sysctl -w net.ipv4.ip_forward=1
for i in all n3 n6 n6m; do sysctl -w net.ipv4.conf.$i.rp_filter=0; done

# Configure policy routing (idempotent)
# Keep N3 symmetric policy routing for GTP-U return traffic.
ip rule show | grep -q "iif n3 lookup 100" || ip rule add iif n3 lookup 100 pref 30
ip route replace default via 10.203.0.1 dev n3 table 100

# Remove legacy rule that can misroute UE-destined return packets into N6.
ip rule del iif n6 lookup 200 2>/dev/null || true
ip route replace default via 10.207.0.1 dev n6 table 200

# Physical RAN return route: when a physical gNB is on a separate subnet,
# the UPF must route GTP-U downlink back through the worker (10.203.0.254)
# which acts as the L3 gateway between the overlay N3 and the RAN transport.
if [ -n "${PHYSICAL_RAN_SUBNET:-}" ]; then
  echo "[UPF][init] Adding return route for physical RAN subnet: ${PHYSICAL_RAN_SUBNET}"
  ip route show | grep -q "${PHYSICAL_RAN_SUBNET}" || \
    ip route add "${PHYSICAL_RAN_SUBNET}" via 10.203.0.254 dev n3
fi

# --- Redirect decapsulated (ogstun) traffic to the Data Network (N6) ---
# Overrides the K3s (eth0) default gateway to prevent leaks onto the management network
echo "[UPF][init] Redirecting default route to N6 Data Network interface..."
ip route replace default via 10.207.0.1 dev n6
# --------------------------------------------------------------------------------

echo "[UPF][init] Starting UPF daemon..."
exec /open5gs/install/bin/open5gs-upfd -c ${UPF_CONFIG:-/etc/open5gs/upf.yaml}

#!/bin/bash
set -e

echo "[UPF][init] Starting UPF initialization..."

# Wait for N3 and N6 interfaces
echo "[UPF][init] Waiting for N3 and N6 interfaces..."
while ! ip addr show n3 | grep -q "inet" || ! ip addr show n6 | grep -q "inet"; do
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

# Start iperf3 server
iperf3 -B 10.45.0.1 -s -fm &

# Configure sysctls
sysctl -w net.ipv4.ip_forward=1
for i in all n3 n6; do sysctl -w net.ipv4.conf.$i.rp_filter=0; done

# Configure policy routing (idempotent)
ip rule show | grep -q "iif n3 lookup 100" || ip rule add iif n3 lookup 100
ip route show table 100 | grep -q "default via 10.203.0.1 dev n3" || ip route add default via 10.203.0.1 dev n3 table 100
ip rule show | grep -q "iif n6 lookup 200" || ip rule add iif n6 lookup 200
ip route show table 200 | grep -q "default via 10.207.0.1 dev n6" || ip route add default via 10.207.0.1 dev n6 table 200

echo "[UPF][init] Redirecting default route to N6 interface..."
ip route replace default via 10.207.0.1 dev n6

echo "[UPF][init] Starting UPF daemon..."
exec /open5gs/install/bin/open5gs-upfd -c ${UPF_CONFIG:-/etc/open5gs/upf.yaml}

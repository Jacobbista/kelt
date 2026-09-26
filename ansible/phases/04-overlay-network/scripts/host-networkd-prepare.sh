#!/usr/bin/env sh
set -e

echo "🔧 Preparing host systemd-networkd to ignore OVS interfaces..."

HOST_NETWORK_DIR="/host/etc/systemd/network"
mkdir -p "$HOST_NETWORK_DIR"

# `Unmanaged=` is a .network option; a .link file cannot carry it. The name sorts
# before the netplan-generated files so it wins the match.
cat > "$HOST_NETWORK_DIR/05-kelt-ovs-unmanaged.network" << 'EOF'
[Match]
Name=br-* vxlan-* ovs-system

[Link]
Unmanaged=yes
EOF
rm -f "$HOST_NETWORK_DIR/99-ovs-unmanaged.link"
echo "  ✅ Written /etc/systemd/network/05-kelt-ovs-unmanaged.network on host"

# (Optional) manage only physical NICs; avoid patterns that match br-*/vxlan-*
# cat > "$HOST_NETWORK_DIR/10-phys.network" << 'EOF'
# [Match]
# Name=en* eth*
#
# [Network]
# DHCP=yes
# EOF

# Reload udev and restart networkd on the host
if command -v nsenter >/dev/null 2>&1; then
  echo "  🔁 Reload udev on host"
  nsenter -t 1 -m -u -i -n -p -- udevadm control --reload || true

  echo "  🔁 Restart systemd-networkd on host"
  nsenter -t 1 -m -u -i -n -p -- /bin/sh -c 'systemctl restart systemd-networkd || systemctl restart systemd-networkd.service' || true
else
  echo "  ⚠️ nsenter not found; cannot restart host services"
fi

echo "  ✅ Host networkd prepared"

#!/usr/bin/env bash
set -Eeuo pipefail

echo "🔧 Configuring kernel/network defaults..."
sysctl -w net.ipv4.ip_forward=1 >/dev/null || true
sysctl -w net.ipv4.conf.all.rp_filter=0 >/dev/null || true
iptables -P FORWARD ACCEPT || true

echo "🔧 Environment:"
echo "  NODE_NAME=${NODE_NAME:-}"
echo "  WORKER_IP=${WORKER_IP:-}"
echo "  EDGE_IP=${EDGE_IP:-}"
echo "  EDGE_ENABLED=${EDGE_ENABLED:-false}"
echo "  CELL_COUNT=${CELL_COUNT:-0}"
echo "  RAN_INTERFACE=${RAN_INTERFACE:-}"
echo "  RAN_BRIDGE_MODE=${RAN_BRIDGE_MODE:-disabled}"
echo "  RAN_SUBNET=${RAN_SUBNET:-}"
# Set by ds-net-setup DaemonSet from Ansible (group_vars); defaults match overlay / ogstun sizing.
OVERLAY_MTU="${OVERLAY_MTU:-1450}"
N6_DATA_MTU="${N6_DATA_MTU:-1400}"
echo "  OVERLAY_MTU=${OVERLAY_MTU}"
echo "  N6_DATA_MTU=${N6_DATA_MTU}"

# VXLAN VNIs per interface (set by the OVS DaemonSet from all.yml; defaults match docs).
N1_VNI="${N1_VNI:-101}"
N2_VNI="${N2_VNI:-102}"
N3_VNI="${N3_VNI:-103}"
N4_VNI="${N4_VNI:-104}"
N6E_VNI="${N6E_VNI:-106}"
N6C_VNI="${N6C_VNI:-107}"
N6M_VNI="${N6M_VNI:-108}"

BRIDGES=(br-n1 br-n2 br-n3 br-n4 br-n6e br-n6c br-n6m)

bridge_mtu_for() {
  local br="$1"
  if [[ "$br" == "br-n6c" ]]; then
    printf '%s' "$N6_DATA_MTU"
  else
    printf '%s' "$OVERLAY_MTU"
  fi
}

create_br() {
  local br="$1"
  local target_mtu="${2:-}"
  if [[ -z "${target_mtu}" ]]; then
    target_mtu="$(bridge_mtu_for "$br")"
  fi
  echo "  -> add-br $br (MTU ${target_mtu})"
  ovs-vsctl --may-exist add-br "$br"
  ip link set "$br" up || true
  ip link set "$br" mtu "$target_mtu" || true
}

ensure_bridge_ip() { # $1=bridge $2=cidr
  local br="$1" cidr="$2"
  if ip -o -4 addr show dev "$br" | awk '{print $4}' | grep -qx "$cidr"; then
    echo "  -> $br already has $cidr"
    return 0
  fi
  echo "  -> assign $cidr to $br"
  ip addr add "$cidr" dev "$br" 2>/dev/null || true
}

calc_local_ip() {
  local peer="$1"
  ip -4 route get "$peer" 2>/dev/null \
    | awk '/src/ {for(i=1;i<=NF;i++) if ($i=="src"){print $(i+1); exit}}'
}

create_vx() { # $1=bridge  $2=ifname  $3=vni  $4=remote_ip  $5=local_ip
  local br="$1" ifn="$2" vni="$3" rip="$4" lip="$5"
  create_br "$br"
  echo "  -> add-port $br $ifn (VNI=$vni remote=$rip local=$lip)"
  ovs-vsctl --may-exist add-port "$br" "$ifn" -- \
    set interface "$ifn" type=vxlan \
      options:key="$vni" \
      options:remote_ip="$rip" \
      options:local_ip="$lip" \
      options:dst_port=4789 \
      options:tos=inherit \
      options:df_default=false
}

# Decide VXLAN peer endpoint (only needed when edge is enabled)
PEER=""
LOCAL_TUN_IP=""
if [[ "${EDGE_ENABLED:-false}" == "true" ]]; then
  if [[ "${NODE_NAME:-}" == "edge" ]]; then
    : "${WORKER_IP:?WORKER_IP required when NODE_NAME=edge}"
    PEER="$WORKER_IP"
  elif [[ "${NODE_NAME:-}" == "worker" ]]; then
    : "${EDGE_IP:?EDGE_IP required when NODE_NAME=worker}"
    PEER="$EDGE_IP"
  else
    echo "❌ NODE_NAME must be 'edge' or 'worker'"; exit 1
  fi

  LOCAL_TUN_IP="$(calc_local_ip "$PEER")"
  if [[ -z "${LOCAL_TUN_IP:-}" ]]; then
    echo "❌ Cannot determine LOCAL_TUN_IP toward $PEER"; exit 1
  fi
  echo "🔧 LOCAL_TUN_IP=$LOCAL_TUN_IP  PEER=$PEER"
elif [[ "${NODE_NAME:-}" != "worker" ]]; then
  echo "❌ NODE_NAME must be 'worker' when edge is disabled"; exit 1
else
  echo "ℹ️  Edge disabled: creating local bridges without VXLAN tunnels"
fi

# Create network bridges (with VXLAN tunnels if edge enabled, local-only otherwise)
echo "🌐 Creating global network bridges..."
if [[ -n "$PEER" ]]; then
  # Edge enabled: create bridges with VXLAN tunnels
  if [[ "$NODE_NAME" == "edge" ]]; then
    create_vx br-n1 vxlan-n1 "$N1_VNI" "$PEER" "$LOCAL_TUN_IP"
    create_vx br-n2 vxlan-n2 "$N2_VNI" "$PEER" "$LOCAL_TUN_IP"
    create_vx br-n3 vxlan-n3 "$N3_VNI" "$PEER" "$LOCAL_TUN_IP"
    create_vx br-n4 vxlan-n4 "$N4_VNI" "$PEER" "$LOCAL_TUN_IP"
    create_vx br-n6e vxlan-n6e "$N6E_VNI" "$PEER" "$LOCAL_TUN_IP"
    create_vx br-n6m vxlan-n6m "$N6M_VNI" "$PEER" "$LOCAL_TUN_IP"
  else
    create_vx br-n1 vxlan-n1 "$N1_VNI" "$PEER" "$LOCAL_TUN_IP"
    create_vx br-n2 vxlan-n2 "$N2_VNI" "$PEER" "$LOCAL_TUN_IP"
    create_vx br-n3 vxlan-n3 "$N3_VNI" "$PEER" "$LOCAL_TUN_IP"
    create_vx br-n4 vxlan-n4 "$N4_VNI" "$PEER" "$LOCAL_TUN_IP"
    create_vx br-n6c vxlan-n6c "$N6C_VNI" "$PEER" "$LOCAL_TUN_IP"
    create_vx br-n6m vxlan-n6m "$N6M_VNI" "$PEER" "$LOCAL_TUN_IP"
  fi
else
  # Edge disabled: create local bridges only (no VXLAN)
  for br in br-n1 br-n2 br-n3 br-n4 br-n6c br-n6m; do
    create_br "$br"
  done
fi

# Assign gateway IPs expected by Whereabouts/IPAM.
# Use worker as gateway owner for shared N1/N2/N3/N4 domains.
if [[ "$NODE_NAME" == "worker" ]]; then
  ensure_bridge_ip br-n1 10.201.0.1/24
  ensure_bridge_ip br-n2 10.202.0.1/24
  ensure_bridge_ip br-n3 10.203.0.1/24
  ensure_bridge_ip br-n4 10.204.0.1/24
  ensure_bridge_ip br-n6c 10.207.0.1/24
  ensure_bridge_ip br-n6m 10.208.0.1/24
elif [[ "$NODE_NAME" == "edge" ]]; then
  # N6e local gateway on edge (MEC side)
  ensure_bridge_ip br-n6e 10.206.0.1/24
fi

# Create per-cell bridges (for N2 and N3 per cell)
if [[ "${CELL_COUNT:-0}" -gt 0 ]]; then
  echo "📱 Creating per-cell network bridges (cells: 1-${CELL_COUNT})..."
  for cell_id in $(seq 1 "$CELL_COUNT"); do
    if [[ -n "$PEER" ]]; then
      # Edge enabled: per-cell bridges use VXLAN between worker and edge.
      vni_n2="${N2_VNI}${cell_id}"
      create_vx "br-n2-cell-${cell_id}" "vxlan-n2-cell-${cell_id}" "$vni_n2" "$PEER" "$LOCAL_TUN_IP"

      vni_n3="${N3_VNI}${cell_id}"
      create_vx "br-n3-cell-${cell_id}" "vxlan-n3-cell-${cell_id}" "$vni_n3" "$PEER" "$LOCAL_TUN_IP"
    else
      # Edge disabled: keep per-cell bridges local-only, same as the global bridges above.
      create_br "br-n2-cell-${cell_id}"
      create_br "br-n3-cell-${cell_id}"
    fi
  done
  echo "✅ Created ${CELL_COUNT} cells (N2 + N3 per cell)"
else
  echo "ℹ️  No per-cell bridges (CELL_COUNT=${CELL_COUNT:-0})"
fi

# ============================================================
# Physical RAN Interface Bridging (Optional)
# ============================================================
# When RAN_BRIDGE_MODE is set, bridge a physical interface to OVS
# for direct femtocell/physical gNB connectivity without NAT/NodePort
#
# Modes:
#   - disabled: No physical RAN bridging (default)
#   - n2_only:  Bridge to N2 network only (control plane)
#   - n3_only:  Bridge to N3 network only (user plane)  
#   - n2_n3:    Bridge to both N2 and N3 (full connectivity)
# ============================================================

bridge_ran_interface() {
  local iface="$1" bridge="$2" tag="${3:-}"
  if ovs-vsctl list-ports "$bridge" | grep -q "^${iface}$"; then
    echo "  -> $iface already on $bridge"
    return 0
  fi
  echo "  -> add-port $bridge $iface (physical RAN)"
  if [[ -n "$tag" ]]; then
    ovs-vsctl --may-exist add-port "$bridge" "$iface" tag="$tag"
  else
    ovs-vsctl --may-exist add-port "$bridge" "$iface"
  fi
  ip link set "$iface" up || true
}

if [[ "${RAN_BRIDGE_MODE:-disabled}" != "disabled" ]] && [[ "$NODE_NAME" == "worker" ]]; then
  RAN_IF="${RAN_INTERFACE:-}"

  # Auto-detect RAN interface from RAN_SUBNET when not explicitly set.
  # Vagrant assigns the worker an IP inside physical_ran_subnet, so we
  # look for the NIC carrying an address in that range.
  if [[ -z "$RAN_IF" ]] && [[ -n "${RAN_SUBNET:-}" ]]; then
    RAN_PREFIX=$(echo "$RAN_SUBNET" | cut -d'/' -f1 | sed 's/\.[0-9]*$//')
    RAN_PREFIX_RE=$(echo "$RAN_PREFIX" | sed 's/\./\\./g')
    RAN_IF=$(ip -o addr show | grep "${RAN_PREFIX_RE}\." | awk '{print $2}' | head -1)
  fi
  
  if [[ -n "$RAN_IF" ]] && ip link show "$RAN_IF" &>/dev/null; then
    echo "🔌 Bridging physical RAN interface: $RAN_IF (mode: ${RAN_BRIDGE_MODE})"
    
    # Remove IP from RAN interface (it will be part of OVS bridge)
    ip addr flush dev "$RAN_IF" 2>/dev/null || true
    
    case "${RAN_BRIDGE_MODE}" in
      n2_only)
        echo "  Mode: N2 only (control plane for NGAP/SCTP)"
        bridge_ran_interface "$RAN_IF" "br-n2"
        ;;
      n3_only)
        echo "  Mode: N3 only (user plane for GTP-U)"
        bridge_ran_interface "$RAN_IF" "br-n3"
        ;;
      n2_n3)
        echo "  Mode: N2+N3 (full connectivity)"
        # For combined mode, we create a dedicated RAN bridge and connect it to both
        create_br "br-ran"
        bridge_ran_interface "$RAN_IF" "br-ran"
        # Create patch ports to connect br-ran to br-n2 and br-n3
        ovs-vsctl --may-exist add-port br-ran patch-ran-n2 -- \
          set interface patch-ran-n2 type=patch options:peer=patch-n2-ran
        ovs-vsctl --may-exist add-port br-n2 patch-n2-ran -- \
          set interface patch-n2-ran type=patch options:peer=patch-ran-n2
        ovs-vsctl --may-exist add-port br-ran patch-ran-n3 -- \
          set interface patch-ran-n3 type=patch options:peer=patch-n3-ran
        ovs-vsctl --may-exist add-port br-n3 patch-n3-ran -- \
          set interface patch-n3-ran type=patch options:peer=patch-ran-n3
        echo "  Created br-ran with patches to br-n2 and br-n3"
        # Assign gateway IP to br-ran so the worker can route between
        # the physical RAN subnet and the overlay N2/N3 networks.
        # The gNB uses this as its default gateway.
        if [[ -n "${RAN_SUBNET:-}" ]]; then
          RAN_GW_IP=$(echo "$RAN_SUBNET" | sed 's|\.[0-9]*/|.1/|')
          ensure_bridge_ip br-ran "$RAN_GW_IP"
          # Secondary router IP on br-n3 for UPF return traffic.
          ensure_bridge_ip br-n3 10.203.0.254/24
        fi
        ;;
      *)
        echo "⚠️  Unknown RAN_BRIDGE_MODE: ${RAN_BRIDGE_MODE}, skipping"
        ;;
    esac
    echo "✅ Physical RAN interface bridged"
  else
    echo "⚠️  RAN interface not found or not available (RAN_IF=${RAN_IF:-none})"
  fi
else
  if [[ "${RAN_BRIDGE_MODE:-disabled}" != "disabled" ]]; then
    echo "ℹ️  RAN bridging only available on worker node"
  fi
  # When RAN_BRIDGE_MODE is disabled, explicitly tear down br-ran if it exists.
  # This ensures Disable (dashboard) leaves no leftover bridge after step 5 restarts the DS pod.
  if [[ "$NODE_NAME" == "worker" ]] && ovs-vsctl br-exists br-ran 2>/dev/null; then
    echo "🧹 Tearing down br-ran (RAN_BRIDGE_MODE=disabled)"
    ovs-vsctl --if-exists del-port br-n2 patch-n2-ran
    ovs-vsctl --if-exists del-port br-n3 patch-n3-ran
    ovs-vsctl --if-exists del-port br-ran patch-ran-n2
    ovs-vsctl --if-exists del-port br-ran patch-ran-n3
    ovs-vsctl --if-exists del-br br-ran
    echo "  -> br-ran removed"
  fi
fi

echo "🔎 OVS interfaces (name/type/ofport):"
ovs-vsctl --columns=name,type,ofport list interface | sed 's/ *\n/\n/g' || true

echo "✅ OVS setup completed"

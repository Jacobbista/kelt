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

# MTUs, VNIs and bridge gateways come from the 5G network plan in all.yml via the
# ds-net-setup DaemonSet env. No fallbacks: a missing value is a deploy bug.
for v in OVERLAY_MTU N6_DATA_MTU \
         N1_VNI N2_VNI N3_VNI N4_VNI N6E_VNI N6C_VNI N6M_VNI \
         N1_GATEWAY_CIDR N2_GATEWAY_CIDR N3_GATEWAY_CIDR N4_GATEWAY_CIDR \
         N6E_GATEWAY_CIDR N6C_GATEWAY_CIDR N6M_GATEWAY_CIDR; do
  : "${!v:?$v must be set by the ds-net-setup DaemonSet}"
  echo "  $v=${!v}"
done

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
  ensure_bridge_ip br-n1 "$N1_GATEWAY_CIDR"
  ensure_bridge_ip br-n2 "$N2_GATEWAY_CIDR"
  ensure_bridge_ip br-n3 "$N3_GATEWAY_CIDR"
  ensure_bridge_ip br-n4 "$N4_GATEWAY_CIDR"
  ensure_bridge_ip br-n6c "$N6C_GATEWAY_CIDR"
  ensure_bridge_ip br-n6m "$N6M_GATEWAY_CIDR"
  # A worker bridge carries only its plane gateway. Older deploys added a
  # secondary N3 address for the UPF return route, which now goes via the gateway.
  for stale in $(ip -o -4 addr show dev br-n3 | awk '{print $4}'); do
    [[ "$stale" == "$N3_GATEWAY_CIDR" ]] || ip addr del "$stale" dev br-n3
  done
elif [[ "$NODE_NAME" == "edge" ]]; then
  # N6e local gateway on edge (MEC side)
  ensure_bridge_ip br-n6e "$N6E_GATEWAY_CIDR"
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

# Per-cell bridges exist only for the cells of an active UERANSIM topology
# (ueransim_active_cells, phase 06 vars): remove those of any other cell, once no
# pod is attached. A bridge removed under an attached pod makes the pod's CNI
# delete fail, so the pod never terminates; phase 05 removes the bridges that
# its AMF rollout has emptied.
for br in $(ovs-vsctl list-br | grep -E '^br-n[23]-cell-[0-9]+$'); do
  cell_id="${br##*-}"
  if [[ "$cell_id" -gt "${CELL_COUNT:-0}" ]]; then
    if [[ -z "$(ovs-vsctl list-ports "$br")" ]]; then
      echo "🧹 Removing $br (cell $cell_id is not active)"
      ovs-vsctl --if-exists del-br "$br"
    else
      echo "ℹ️  Keeping $br until its pods detach (cell $cell_id is not active)"
    fi
  fi
done

# ============================================================
# Physical RAN Interface Bridging (Optional)
# ============================================================
# RAN_BRIDGE_MODE (ran_bridge_mode in all.yml):
#   - disabled: no RAN bridge; an existing br-ran is torn down
#   - n2_n3:    br-ran carries the worker's RAN NIC and the RAN gateway address.
#               N2 reaches the AMF on its own br-ran interface (n2ran, NAD n2-ran);
#               N3 is routed by the worker from br-ran to br-n3. br-ran has no
#               layer-2 link to any plane bridge.
# See docs/deployment/physical-ran.md
# ============================================================

bridge_ran_interface() {
  local iface="$1" bridge="$2" tag="${3:-}"
  if ovs-vsctl list-ports "$bridge" | grep -q "^${iface}$"; then
    echo "  -> $iface already on $bridge"
  else
    echo "  -> add-port $bridge $iface (physical RAN)"
    if [[ -n "$tag" ]]; then
      ovs-vsctl --may-exist add-port "$bridge" "$iface" tag="$tag"
    else
      ovs-vsctl --may-exist add-port "$bridge" "$iface"
    fi
  fi
  # networkd does not manage the NIC (see below), so after a reboot nothing
  # else brings it up while OVS still lists it as a port.
  ip link set "$iface" up || true
}

if [[ "${RAN_BRIDGE_MODE:-disabled}" != "disabled" ]] && [[ "$NODE_NAME" == "worker" ]]; then
  RAN_IF="${RAN_INTERFACE:-}"

  # Auto-detect the RAN NIC when not explicitly set. Once bridged it is the
  # physical port of br-ran (kept in the OVS database across reboots; a physical
  # NIC has a sysfs device link, ovs-cni veths do not). On the first setup it is
  # the physical NIC carrying the address Vagrant gave it in physical_ran_subnet.
  if [[ -z "$RAN_IF" ]] && ovs-vsctl br-exists br-ran 2>/dev/null; then
    for p in $(ovs-vsctl list-ports br-ran); do
      [[ -e "/sys/class/net/$p/device" ]] && { RAN_IF="$p"; break; }
    done
  fi
  if [[ -z "$RAN_IF" ]] && [[ -n "${RAN_SUBNET:-}" ]]; then
    RAN_PREFIX=$(echo "$RAN_SUBNET" | cut -d'/' -f1 | sed 's/\.[0-9]*$//')
    RAN_PREFIX_RE=$(echo "$RAN_PREFIX" | sed 's/\./\\./g')
    for p in $(ip -o addr show | grep "${RAN_PREFIX_RE}\." | awk '{print $2}'); do
      [[ -e "/sys/class/net/$p/device" ]] && { RAN_IF="$p"; break; }
    done
  fi

  if [[ -n "$RAN_IF" ]] && ip link show "$RAN_IF" &>/dev/null; then
    echo "🔌 Bridging physical RAN interface: $RAN_IF (mode: ${RAN_BRIDGE_MODE})"

    # The NIC is an OVS port: its address belongs on br-ran. The Vagrant netplan
    # configures it with that address, and systemd-networkd re-applies it whenever
    # it restarts, which sends traffic out of the NIC and around the bridge. Take
    # the NIC out of networkd's hands first, then remove the address.
    # See docs/deployment/physical-ran.md (Who Owns the RAN Address)
    printf '[Match]\nName=%s\n\n[Link]\nUnmanaged=yes\n' "$RAN_IF" \
      > /host/etc/systemd/network/05-kelt-ran-unmanaged.network
    nsenter -t 1 -m -u -i -n -p -- networkctl reload || true
    # The reload is asynchronous: flush only once networkd has let go of the NIC.
    for _ in $(seq 1 20); do
      nsenter -t 1 -m -u -i -n -p -- networkctl list "$RAN_IF" 2>/dev/null | grep -q unmanaged && break
      sleep 0.5
    done
    ip addr flush dev "$RAN_IF" 2>/dev/null || true
    
    case "${RAN_BRIDGE_MODE}" in
      n2_n3)
        create_br "br-ran"
        bridge_ran_interface "$RAN_IF" "br-ran"
        # Earlier versions joined br-ran to br-n2 and br-n3 with patch ports,
        # which made the RAN segment and both planes one layer-2 domain. Remove
        # them where they are still present.
        ovs-vsctl --if-exists del-port br-n2 patch-n2-ran
        ovs-vsctl --if-exists del-port br-n3 patch-n3-ran
        ovs-vsctl --if-exists del-port br-ran patch-ran-n2
        ovs-vsctl --if-exists del-port br-ran patch-ran-n3
        # The RAN gateway: the gNB's default gateway, through which the worker
        # routes GTP-U to br-n3. The UPF reaches the RAN back through the N3 gateway.
        ensure_bridge_ip br-ran "$RAN_GATEWAY_CIDR"
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
  # Hand the RAN NIC back to networkd (the Vagrant netplan restores its address).
  if [[ "$NODE_NAME" == "worker" ]] && [[ -f /host/etc/systemd/network/05-kelt-ran-unmanaged.network ]]; then
    rm -f /host/etc/systemd/network/05-kelt-ran-unmanaged.network
    nsenter -t 1 -m -u -i -n -p -- networkctl reload || true
    echo "  -> RAN NIC returned to systemd-networkd"
  fi
fi

# ============================================================
# Plane isolation filter (worker only)
# ============================================================
# The worker owns the gateway of every overlay bridge, so it can route between
# any two planes. The filter sees only traffic entering or leaving a plane
# bridge (br-*); everything else goes on to the Kubernetes rules untouched.
# Allowed crossings are the transports the architecture has; any other crossing
# is counted per pair of planes (or plane and non-plane interface) and, in
# enforce mode, dropped.
# It lives in its own nftables table, hooked before iptables: the iptables
# filter table is rewritten periodically by the k3s network components, which
# resets counters and could reorder rules.
# See docs/architecture/plane-isolation.md
apply_plane_filter() {
  local mode="$1" verdict egress br ruleset=/tmp/kelt-planes.nft
  case "$mode" in
    observe) verdict="accept" ;;
    enforce) verdict="drop" ;;
    *) echo "❌ PLANE_FILTER_MODE must be observe or enforce, got '$mode'"; exit 1 ;;
  esac
  egress="$(ip route show default 2>/dev/null | awk '/default/ {print $5; exit}')"

  local bridges a b
  bridges="$(ls /sys/class/net | grep '^br-')"

  # A not-allowed match is two rules: a sampling rule with its own rate limit
  # (one rule's burst never hides another's samples in the kernel log), then the
  # counting rule. The log prefix carries the rule, spaces as underscores.
  deny() {  # $1 = nft match, $2 = rule text after "not allowed: "
    echo "    $1 limit rate 6/minute log prefix \"KELT-PLANES $mode ${2// /_}: \""
    echo "    $1 counter jump not_allowed comment \"not allowed: $2\""
  }

  {
    # Declare then delete so the replace below is atomic and idempotent.
    echo "table inet kelt_planes"
    echo "delete table inet kelt_planes"
    echo "table inet kelt_planes {"
    echo "  chain not_allowed {"
    echo "    $verdict"
    echo "  }"
    echo "  chain forward {"
    echo "    type filter hook forward priority filter - 10; policy accept;"
    # UE addresses live behind the UPF: seeing them outside the plane bridges
    # (for example an app answering a UE through its pod network) is a leak.
    deny "ip daddr { $PLANE_UE_POOLS } oifname != \"br-*\"" "to UE pools outside the planes"
    deny "ip saddr { $PLANE_UE_POOLS } iifname != \"br-*\"" "from UE pools outside the planes"
    echo "    iifname != \"br-*\" oifname != \"br-*\" accept"
    # Physical RAN transport toward the UPF (N3, GTP-U), when the RAN bridge
    # exists (physical RAN on). N2 needs no crossing: the AMF has its own
    # interface on br-ran.
    if [[ " $(echo $bridges) " == *" br-ran "* ]]; then
      echo "    iifname \"br-ran\" oifname \"br-n3\" counter accept comment \"allowed: br-ran -> br-n3\""
      echo "    iifname \"br-n3\" oifname \"br-ran\" counter accept comment \"allowed: br-n3 -> br-ran\""
    fi
    # N6c internet breakout (NAT on the egress interface).
    if [[ -n "$egress" ]]; then
      echo "    iifname \"br-n6c\" oifname \"$egress\" counter accept comment \"allowed: br-n6c -> $egress\""
      echo "    iifname \"$egress\" oifname \"br-n6c\" ct state established,related counter accept comment \"allowed: $egress -> br-n6c replies\""
    fi
    # Everything else is not allowed, counted where it was headed: one rule per
    # ordered pair of planes, then plane -> anything else (host, pod network),
    # then anything else -> a plane.
    for a in $bridges; do
      for b in $bridges; do
        [[ "$a" == "$b" ]] || deny "iifname \"$a\" oifname \"$b\"" "$a -> $b"
      done
    done
    for a in $bridges; do
      deny "iifname \"$a\"" "from $a"
    done
    deny "oifname \"br-*\"" "into a plane"
    echo "  }"
    echo "}"
  } > "$ruleset"
  nft -f "$ruleset"

  # Earlier deploys kept the filter as an iptables chain; remove it.
  iptables -D FORWARD -j KELT-PLANES 2>/dev/null || true
  iptables -F KELT-PLANES 2>/dev/null || true
  iptables -X KELT-PLANES 2>/dev/null || true

  echo "🧱 Plane filter applied (mode: $mode, egress: ${egress:-none})"
}

if [[ "$NODE_NAME" == "worker" ]]; then
  : "${PLANE_FILTER_MODE:?PLANE_FILTER_MODE must be set by the ds-net-setup DaemonSet}"
  : "${PLANE_UE_POOLS:?PLANE_UE_POOLS must be set by the ds-net-setup DaemonSet}"
  apply_plane_filter "$PLANE_FILTER_MODE"
fi

echo "🔎 OVS interfaces (name/type/ofport):"
ovs-vsctl --columns=name,type,ofport list interface | sed 's/ *\n/\n/g' || true

echo "✅ OVS setup completed"

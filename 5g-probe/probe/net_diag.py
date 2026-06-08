"""
Host and network-namespace interface diagnostics for 5g-probe.

Used to classify UE attachment, suggest management UI targets, and size UDP payloads.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# iproute2 helpers (callers pass text from `ip` commands)
# ---------------------------------------------------------------------------


def parse_ipv4_addr_show(text: str) -> Optional[Tuple[str, int]]:
    """First global IPv4 address on interface from `ip -4 addr show dev X` output."""
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)\s", text)
    if not m:
        return None
    return m.group(1), int(m.group(2))


def parse_link_mtu(text: str) -> int:
    m = re.search(r"\bmtu (\d+)\b", text)
    return int(m.group(1)) if m else 1500


def parse_route_get_dev_mtu(text: str) -> Tuple[Optional[str], int]:
    """Parse `ip route get <host>` for `dev` and `mtu` if present."""
    dm = re.search(r"\bdev (\S+)", text)
    mm = re.search(r"\bmtu (\d+)\b", text)
    dev = dm.group(1) if dm else None
    mtu = int(mm.group(1)) if mm else 1500
    return dev, mtu


def is_rfc1918(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.is_private


def subnet_and_network(ip_s: str, plen: int) -> Tuple[str, Optional[ipaddress.IPv4Network]]:
    """Human-readable CIDR and network object (None if host-only trick)."""
    try:
        iface = ipaddress.ip_interface(f"{ip_s}/{plen}")
        net = iface.network
        if isinstance(net, ipaddress.IPv4Network):
            return str(net), net
    except ValueError:
        pass
    return f"{ip_s}/{plen}", None


def management_candidates(ip_s: str, plen: int, gateway: Optional[str]) -> List[str]:
    """Ordered management UI candidates (deduplicated)."""
    seen: List[str] = []
    _, net = subnet_and_network(ip_s, plen)

    def add(x: Optional[str]) -> None:
        if x and x not in seen:
            seen.append(x)

    add(gateway)
    if net and net.prefixlen <= 30 and net.num_addresses >= 2:
        # First host address in subnet (often .1 on /24).
        try:
            add(str(list(net.hosts())[0]))
        except (ValueError, IndexError):
            pass
    if is_rfc1918(ip_s):
        add("192.168.1.1")
    return seen


def topology_hint(
    iface_type: str,
    ip_s: Optional[str],
    plen: Optional[int],
    gateway: Optional[str],
    realtek_router_hint: bool,
) -> str:
    if iface_type == "wwan":
        return "wwan_pdu"
    if realtek_router_hint:
        return "tether"
    if not ip_s or plen is None:
        return "unknown"
    try:
        iface_addr = ipaddress.ip_address(ip_s)
    except ValueError:
        return "unknown"
    _, net = subnet_and_network(ip_s, plen)
    if net and gateway:
        try:
            gw_ip = ipaddress.ip_address(gateway)
            if gw_ip in net and iface_addr.is_private:
                return "dongle_lan"
        except ValueError:
            pass
    if plen >= 31 and not iface_addr.is_private:
        return "wwan_pdu"
    return "unknown"


def diagnostics_dict(
    iface: str,
    *,
    iface_type: str,
    vendor_mac_hint_router: bool,
    addr_show: str,
    route_show_dev: str,
    link_show: str,
    route_get_target: Optional[str] = None,
    route_get_out: Optional[str] = None,
) -> Dict[str, Any]:
    """Build API-ready diagnostics for one interface."""
    parsed = parse_ipv4_addr_show(addr_show)
    mtu = parse_link_mtu(link_show)
    gw_m = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", route_show_dev)
    gateway = gw_m.group(1) if gw_m else None

    ip_s: Optional[str] = None
    plen: Optional[int] = None
    subnet_s: Optional[str] = None
    if parsed:
        ip_s, plen = parsed
        subnet_s, _ = subnet_and_network(ip_s, plen)

    candidates: List[str] = []
    if ip_s is not None and plen is not None:
        candidates = management_candidates(ip_s, plen, gateway)

    hint = topology_hint(iface_type, ip_s, plen, gateway, vendor_mac_hint_router)

    route_mtu: Optional[int] = None
    if route_get_out:
        _, route_mtu = parse_route_get_dev_mtu(route_get_out)

    return {
        "name": iface,
        "ipv4": ip_s or "",
        "prefix_len": plen if plen is not None else None,
        "subnet": subnet_s or "",
        "gateway": gateway or "",
        "mtu": mtu,
        "route_mtu": route_mtu,
        "topology_hint": hint,
        "management_candidates": candidates,
    }


def udp_payload_from_path_mtu(route_mtu: Optional[int], iface_mtu: int, clamp_max: int = 1200) -> int:
    """
    Conservative UDP payload (iperf -l) from MTU toward target.

    Uses IPv4 header + UDP header = 28 bytes. Result is clamped to clamp_max
    so tunnel overhead beyond link MTU does not fragment single-datagram tests.
    """
    mtu = route_mtu if route_mtu else iface_mtu
    raw = mtu - 28
    payload = max(68, min(raw, clamp_max))
    return payload

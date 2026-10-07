"""N-interface connectivity health checks.

Runs targeted probes from inside NF pods to verify each 5G reference-point
link (N2, N3, N4, N6) is operational.  Uses the K8s Python client ``stream``
API (same approach as ``ue_service._exec_in_pod``).
"""

import ipaddress
import logging
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from kubernetes.stream import stream

from app.config import settings
from app.services.k8s_service import K8sService, nf_api_get, thread_core
from app.services.network_plan import load_plan, plan_value

log = logging.getLogger(__name__)

NS = plan_value("namespace_5g")

_RE_LATENCY = re.compile(r"time[=<]([\d.]+)\s*ms")

# RFC 1918 private address blocks — any RETURN rule whose destination is a
# subnet of one of these is treated as a private-network bypass rule.
_RFC1918 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


def _is_private(dst: str) -> bool:
    """Return True if dst (CIDR notation) is a subnet of an RFC 1918 block."""
    try:
        net = ipaddress.ip_network(dst, strict=False)
        return any(net.subnet_of(r) for r in _RFC1918)
    except ValueError:
        return False


def _read_ips() -> dict[str, str]:
    """Fixed endpoints and gateways from the network plan (all.yml)."""
    data = load_plan()
    return {
        "smf_n4": data["smf_n4_ip"],
        "upf_cloud_n3": data["upf_cloud_n3_ip"],
        "upf_cloud_n4": data["upf_cloud_n4_ip"],
        "upf_edge_n4": data["upf_edge_n4_ip"],
        "n3_gw": data["n3_gateway"],
        "n6c_gw": data["n6c_gateway"],
    }


def _exec(core, pod: str, container: str, cmd: list[str]) -> str:
    try:
        return stream(
            core.connect_get_namespaced_pod_exec,
            pod, NS, command=cmd,
            container=container,
            stderr=True, stdout=True, stdin=False, tty=False,
            _request_timeout=8,
        )
    except Exception as exc:
        # websocket-client / ApiException handshake errors carry the response
        # headers and body after " -+-+- " and across newlines; keep only the
        # short summary so the UI never shows the raw header dump.
        msg = str(exc).split(" -+-+- ")[0].replace("\n", " ").strip()
        return f"ERROR: {msg[:160]}"


def _find_pod(core, app: str) -> tuple[str, str] | None:
    """Return (pod_name, container_name) for the first Running pod matching label app=<app>."""
    pods = core.list_namespaced_pod(namespace=NS, label_selector=f"app={app}")
    for p in pods.items:
        if p.metadata.deletion_timestamp:
            continue
        if p.status.phase == "Running":
            return p.metadata.name, app
    return None


def _nf_api_get(core, app: str, port: int | str, path: str) -> dict[str, Any]:
    """Call NF management HTTP endpoint via K8s API server pod proxy."""
    return nf_api_get(core, NS, app, port, path)


class NetworkHealthService:
    # The routers build a service per request, so the last run is kept on the
    # class: every page polling GET /network/health shares one run, and the status
    # summary reads it without starting one (the checks exec into pods).
    _last: list[dict[str, Any]] = []
    _last_at: float = 0.0
    _run_lock = threading.Lock()  # concurrent stale requests share one run

    def __init__(self, k8s: K8sService) -> None:
        self.k8s = k8s

    def get_cached(self, max_age: float | None = None) -> list[dict[str, Any]]:
        """The last run, or nothing when it is older than `max_age` seconds."""
        if max_age is not None and time.monotonic() - NetworkHealthService._last_at > max_age:
            return []
        return list(NetworkHealthService._last)

    def latest(self, max_age: float) -> list[dict[str, Any]]:
        """The last run when younger than `max_age` seconds, otherwise a new one."""
        fresh = self.get_cached(max_age)
        if fresh:
            return fresh
        with NetworkHealthService._run_lock:
            # Another request may have run the checks while this one waited.
            return self.get_cached(max_age) or self.run_health_checks()

    def last_age(self) -> float | None:
        """Seconds since the last run, None before the first."""
        return time.monotonic() - NetworkHealthService._last_at if NetworkHealthService._last else None

    def run_health_checks(self) -> list[dict[str, Any]]:
        ips = _read_ips()
        checks: list[dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(self._check_n2, ips): "N2",
                pool.submit(self._check_n3, ips): "N3",
                pool.submit(self._check_n4, ips): "N4",
                pool.submit(self._check_n6, ips): "N6",
            }
            for fut in as_completed(futures):
                label = futures[fut]
                try:
                    checks.append(fut.result())
                except Exception as exc:
                    checks.append({
                        "interface": label,
                        "bridge": f"br-{label.lower().replace('-', '')}",
                        "status": "error",
                        "detail": str(exc),
                        "latency_ms": None,
                    })

        checks.sort(key=lambda c: c["interface"])
        NetworkHealthService._last = checks
        NetworkHealthService._last_at = time.monotonic()
        return checks

    def _ssh(self, command: str, timeout: int | None = None) -> str:
        proc = subprocess.run(
            [
                "ssh",
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "LogLevel=ERROR",
                "-o", "BatchMode=yes",
                settings.worker_ssh_host,
                command,
            ],
            capture_output=True,
            text=True,
            timeout=timeout or settings.shell_timeout_seconds,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"SSH failed ({proc.returncode}): {err}")
        return proc.stdout or ""

    @staticmethod
    def _parse_nat_rule(raw: str, source: str) -> dict[str, Any]:
        parts = raw.split()
        out_if = ""
        dst = ""
        action = ""
        for i, tok in enumerate(parts):
            if tok == "-o" and i + 1 < len(parts):
                out_if = parts[i + 1]
            elif tok == "-d" and i + 1 < len(parts):
                dst = parts[i + 1]
            elif tok == "-j" and i + 1 < len(parts):
                action = parts[i + 1]
        rule_type = "other"
        if action == "RETURN" and _is_private(dst):
            rule_type = "private_bypass"
        elif action == "MASQUERADE":
            rule_type = "public_masquerade"
        return {
            "raw": raw,
            "source": source,
            "dest": dst,
            "out_if": out_if,
            "action": action,
            "type": rule_type,
        }

    def get_n6_nat_diagnostics(self) -> dict[str, Any]:
        """Collect runtime NAT policy state for N6 egress on worker."""
        try:
            ipt_alt = self._ssh("readlink -f /etc/alternatives/iptables 2>/dev/null || true").strip()
            preferred_backend = "nft" if "nft" in ipt_alt else ("legacy" if "legacy" in ipt_alt else "unknown")
            ip_forward_raw = self._ssh("sysctl -n net.ipv4.ip_forward 2>/dev/null || echo 0").strip()
            ip_forward_enabled = ip_forward_raw == "1"
            out_if = self._ssh("ip route show default 2>/dev/null | awk '/default/ {print $5; exit}'").strip()

            nft_rules_out = self._ssh("sudo iptables-nft -t nat -S POSTROUTING 2>/dev/null || true")
            legacy_rules_out = self._ssh("sudo iptables-legacy -t nat -S POSTROUTING 2>/dev/null || true")
        except Exception as exc:
            return {
                "summary": {
                    "status": "error",
                    "backend": "unknown",
                    "ip_forward_enabled": False,
                    "outbound_interface": "",
                },
                "rules": [],
                "legacy_rules": [],
                "checks": {},
                "warnings": [str(exc)],
            }

        n6_subnet = plan_value("n6c_subnet")
        nft_n6 = [ln.strip() for ln in nft_rules_out.splitlines() if n6_subnet in ln and ln.strip().startswith("-A ")]
        legacy_n6 = [ln.strip() for ln in legacy_rules_out.splitlines() if n6_subnet in ln and ln.strip().startswith("-A ")]
        active_source = "nft" if preferred_backend == "nft" else ("legacy" if preferred_backend == "legacy" else "nft")
        active_raw = nft_n6 if active_source == "nft" else legacy_n6

        rules = [self._parse_nat_rule(r, active_source) for r in active_raw]
        counts: dict[str, int] = {}
        for r in active_raw:
            counts[r] = counts.get(r, 0) + 1
        duplicates = sorted([raw for raw, cnt in counts.items() if cnt > 1])
        for r in rules:
            r["duplicate"] = counts.get(r["raw"], 0) > 1

        # Verify that at least one RETURN rule covers each of the 3 RFC 1918 blocks.
        covered: set[int] = set()
        for r in rules:
            if r["type"] == "private_bypass":
                try:
                    net = ipaddress.ip_network(r["dest"], strict=False)
                    for idx, block in enumerate(_RFC1918):
                        if net.subnet_of(block):
                            covered.add(idx)
                except ValueError:
                    pass
        required_private_ok = covered == {0, 1, 2}
        has_masquerade = any(r["action"] == "MASQUERADE" for r in rules)
        has_legacy_leftovers = len(legacy_n6) > 0 if active_source == "nft" else False

        warnings: list[str] = []
        if not ip_forward_enabled:
            warnings.append("IP forwarding is disabled on worker (net.ipv4.ip_forward != 1).")
        if not out_if:
            warnings.append("Default outbound interface is missing.")
        if not required_private_ok:
            warnings.append("One or more private-network bypass RETURN rules are missing.")
        if not has_masquerade:
            warnings.append("MASQUERADE catch-all rule is missing for N6 egress.")
        if duplicates:
            warnings.append("Duplicate N6 NAT rules detected in active backend.")
        if has_legacy_leftovers:
            warnings.append("Legacy backend still contains N6 rules; backend state is mixed.")

        status = "ok" if not warnings else "warn"
        return {
            "summary": {
                "status": status,
                "backend": preferred_backend,
                "ip_forward_enabled": ip_forward_enabled,
                "outbound_interface": out_if,
                "n6_subnet": n6_subnet,
            },
            "rules": rules,
            "legacy_rules": legacy_n6,
            "checks": {
                "ip_forward_enabled": ip_forward_enabled,
                "private_bypass_complete": required_private_ok,
                "masquerade_present": has_masquerade,
                "duplicates_present": bool(duplicates),
                "legacy_leftovers_present": has_legacy_leftovers,
            },
            "warnings": warnings,
        }

    def _check_n2(self, ips: dict[str, str]) -> dict[str, Any]:
        """AMF must be listening on SCTP port 38412."""
        result = {"interface": "N2", "bridge": "br-n2", "status": "unknown", "detail": "", "latency_ms": None}
        # N2 check via AMF gnb-info (K8s API proxy, thread-safe client).
        # A connected gNB with setup_success=true confirms N2/NGAP is operational.
        data = _nf_api_get(thread_core(), "amf", "metrics", "gnb-info")
        gnbs = data.get("items", [])
        connected = [g for g in gnbs if g.get("ng", {}).get("setup_success")]
        if not data:
            result["status"] = "error"
            result["detail"] = "AMF gnb-info unreachable"
        elif connected:
            result["status"] = "ok"
            result["detail"] = f"SCTP established — {len(connected)} gNB(s) connected"
        elif gnbs:
            result["status"] = "warn"
            result["detail"] = f"{len(gnbs)} gNB(s) seen but none with setup_success"
        else:
            result["status"] = "fail"
            result["detail"] = "No gNBs connected on N2"
        return result

    def _check_n3(self, ips: dict[str, str]) -> dict[str, Any]:
        """N3 gateway reachable from netshoot pod via n3 interface."""
        result = {"interface": "N3", "bridge": "br-n3", "status": "unknown", "detail": "", "latency_ms": None}
        pod_info = _find_pod(thread_core(), "netshoot")
        if not pod_info:
            result["status"] = "error"
            result["detail"] = "netshoot pod not running"
            return result
        pod, container = pod_info

        out = _exec(thread_core(), pod, container,
                     ["ping", "-c", "1", "-W", "2", "-I", "n3", ips["n3_gw"]])
        m = _RE_LATENCY.search(out)
        if "1 received" in out or "1 packets received" in out:
            result["status"] = "ok"
            result["detail"] = f"N3 gateway {ips['n3_gw']} reachable"
            if m:
                result["latency_ms"] = float(m.group(1))
        elif "ERROR" in out:
            result["status"] = "error"
            result["detail"] = out[:200]
        else:
            result["status"] = "fail"
            result["detail"] = f"N3 gateway {ips['n3_gw']} unreachable; GTP-U 2152 not found"

        return result

    def _check_n4(self, ips: dict[str, str]) -> dict[str, Any]:
        """N4 check via SMF pdu-info (K8s API proxy, thread-safe client).
        Active PDU sessions confirm PFCP is operational.
        N4 IP pool is fully allocated to NFs so ping is not viable."""
        result = {"interface": "N4", "bridge": "br-n4", "status": "unknown", "detail": "", "latency_ms": None}
        data = _nf_api_get(thread_core(), "smf", "metrics", "pdu-info?page=0&page_size=1")
        if not data:
            result["status"] = "error"
            result["detail"] = "SMF pdu-info unreachable"
        elif data.get("pager", {}).get("count", 0) > 0:
            count = data["pager"]["count"]
            result["status"] = "ok"
            result["detail"] = f"PFCP active — {count} PDU session(s) via UPF {ips['upf_cloud_n4']}"
        else:
            result["status"] = "warn"
            result["detail"] = "SMF reachable but no active PDU sessions"
        return result

    def _check_n6(self, ips: dict[str, str]) -> dict[str, Any]:
        """N6 gateway reachable from netshoot pod via n6 interface."""
        result = {"interface": "N6", "bridge": "br-n6c", "status": "unknown", "detail": "", "latency_ms": None}
        pod_info = _find_pod(thread_core(), "netshoot")
        if not pod_info:
            result["status"] = "error"
            result["detail"] = "netshoot pod not running"
            return result
        pod, container = pod_info

        out = _exec(thread_core(), pod, container,
                     ["ping", "-c", "1", "-W", "2", "-I", "n6c", ips["n6c_gw"]])
        m = _RE_LATENCY.search(out)
        if "1 received" in out or "1 packets received" in out:
            result["status"] = "ok"
            result["detail"] = f"N6 gateway {ips['n6c_gw']} reachable"
            if m:
                result["latency_ms"] = float(m.group(1))
        elif "ERROR" in out:
            result["status"] = "error"
            result["detail"] = out[:200]
        else:
            result["status"] = "fail"
            result["detail"] = f"N6 gateway {ips['n6c_gw']} unreachable"

        return result

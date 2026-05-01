"""N-interface connectivity health checks.

Runs targeted probes from inside NF pods to verify each 5G reference-point
link (N2, N3, N4, N6) is operational.  Uses the K8s Python client ``stream``
API (same approach as ``ue_service._exec_in_pod``).
"""

import ipaddress
import logging
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yaml
from kubernetes.stream import stream

from app.config import settings
from app.services.k8s_service import K8sService

log = logging.getLogger(__name__)

NS = "5g"
GROUP_VARS = Path("/home/vagrant/ansible-ro/group_vars/all.yml")

_RE_LATENCY = re.compile(r"time[=<]([\d.]+)\s*ms")
_N6_SUBNET = "10.207.0.0/24"

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
    """Pull static IPs from Ansible group_vars."""
    try:
        data = yaml.safe_load(GROUP_VARS.read_text()) or {}
    except FileNotFoundError:
        data = {}
    return {
        "smf_n4": data.get("smf_n4_ip", "10.204.0.100"),
        "upf_cloud_n3": data.get("upf_cloud_n3_ip", "10.203.0.101"),
        "upf_cloud_n4": data.get("upf_cloud_n4_ip", "10.204.0.101"),
        "upf_edge_n4": data.get("upf_edge_n4_ip", "10.204.0.102"),
        "n3_gw": data.get("n3_gateway", "10.203.0.1"),
        "n6c_gw": data.get("n6c_gateway", "10.207.0.1"),
    }


def _exec(core, pod: str, container: str, cmd: list[str]) -> str:
    # Retry once on transient WebSocket handshake failures (K8s API returns
    # 200 instead of 101 under load; a brief pause usually resolves it).
    for attempt in range(2):
        try:
            return stream(
                core.connect_get_namespaced_pod_exec,
                pod, NS, command=cmd,
                stderr=True, stdout=True, stdin=False, tty=False,
                _request_timeout=8,
            )
        except Exception as exc:
            msg = str(exc)
            if " -+-+- " in msg:
                msg = msg.split(" -+-+- ")[0].strip()
            if attempt == 0 and "Handshake status" in msg:
                time.sleep(1)
                continue
            return f"ERROR: {msg}"
    return "ERROR: exec unavailable after retry"


def _find_pod(core, app: str) -> tuple[str, str] | None:
    """Return (pod_name, container_name) for the first Running pod matching label app=<app>."""
    pods = core.list_namespaced_pod(namespace=NS, label_selector=f"app={app}")
    for p in pods.items:
        if p.metadata.deletion_timestamp:
            continue
        if p.status.phase == "Running":
            container = app
            return p.metadata.name, container
    return None


class NetworkHealthService:
    def __init__(self, k8s: K8sService) -> None:
        self.k8s = k8s
        self._cache: list[dict[str, Any]] = []
        self._cache_ts: float = 0.0

    def get_cached(self) -> list[dict[str, Any]]:
        return list(self._cache)

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
        self._cache = checks
        self._cache_ts = time.monotonic()
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

        nft_n6 = [ln.strip() for ln in nft_rules_out.splitlines() if _N6_SUBNET in ln and ln.strip().startswith("-A ")]
        legacy_n6 = [ln.strip() for ln in legacy_rules_out.splitlines() if _N6_SUBNET in ln and ln.strip().startswith("-A ")]
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
        pod_info = _find_pod(self.k8s.core, "amf")
        if not pod_info:
            result["status"] = "error"
            result["detail"] = "AMF pod not running"
            return result
        pod, container = pod_info
        out = _exec(self.k8s.core, pod, container, ["ss", "-Slnp"])
        if "38412" in out:
            result["status"] = "ok"
            result["detail"] = "SCTP 38412 listening"
        else:
            result["status"] = "fail"
            result["detail"] = "SCTP 38412 not found"
        return result

    def _check_n3(self, ips: dict[str, str]) -> dict[str, Any]:
        """UPF-Cloud must have N3 gateway reachable and GTP-U port open."""
        result = {"interface": "N3", "bridge": "br-n3", "status": "unknown", "detail": "", "latency_ms": None}
        pod_info = _find_pod(self.k8s.core, "upf-cloud")
        if not pod_info:
            result["status"] = "error"
            result["detail"] = "UPF-Cloud pod not running"
            return result
        pod, container = pod_info

        out = _exec(self.k8s.core, pod, container,
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
            result["detail"] = f"N3 gateway {ips['n3_gw']} unreachable"

        ss_out = _exec(self.k8s.core, pod, container, ["ss", "-unap"])
        if "2152" in ss_out:
            result["detail"] += "; GTP-U 2152 listening"
        else:
            if result["status"] == "ok":
                result["status"] = "warn"
            result["detail"] += "; GTP-U 2152 not found"

        return result

    def _check_n4(self, ips: dict[str, str]) -> dict[str, Any]:
        """SMF must reach UPF-Cloud on PFCP port 8805 via N4."""
        result = {"interface": "N4", "bridge": "br-n4", "status": "unknown", "detail": "", "latency_ms": None}
        pod_info = _find_pod(self.k8s.core, "smf")
        if not pod_info:
            result["status"] = "error"
            result["detail"] = "SMF pod not running"
            return result
        pod, container = pod_info

        t0 = time.monotonic()
        out = _exec(self.k8s.core, pod, container,
                     ["ping", "-c", "1", "-W", "2", "-I", "n4", ips["upf_cloud_n4"]])
        elapsed = (time.monotonic() - t0) * 1000

        if "1 received" in out or "1 packets received" in out:
            m = _RE_LATENCY.search(out)
            result["status"] = "ok"
            result["detail"] = f"UPF PFCP {ips['upf_cloud_n4']}:8805 reachable"
            result["latency_ms"] = float(m.group(1)) if m else round(elapsed, 1)
        elif "ERROR" in out:
            result["status"] = "error"
            result["detail"] = out[:200]
        else:
            result["status"] = "fail"
            result["detail"] = f"UPF {ips['upf_cloud_n4']} unreachable on N4"

        return result

    def _check_n6(self, ips: dict[str, str]) -> dict[str, Any]:
        """UPF-Cloud must reach the N6 gateway (data network egress)."""
        result = {"interface": "N6", "bridge": "br-n6c", "status": "unknown", "detail": "", "latency_ms": None}
        pod_info = _find_pod(self.k8s.core, "upf-cloud")
        if not pod_info:
            result["status"] = "error"
            result["detail"] = "UPF-Cloud pod not running"
            return result
        pod, container = pod_info

        out = _exec(self.k8s.core, pod, container,
                     ["ping", "-c", "1", "-W", "2", "-I", "n6", ips["n6c_gw"]])
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

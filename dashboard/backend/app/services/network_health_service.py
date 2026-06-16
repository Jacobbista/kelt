"""N-interface connectivity health checks.

Runs targeted probes from inside NF pods to verify each 5G reference-point
link (N2, N3, N4, N6) is operational.  Uses the K8s Python client ``stream``
API (same approach as ``ue_service._exec_in_pod``).
"""

import ipaddress
import json
import logging
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yaml
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.stream import stream

from app.config import settings
from app.services.k8s_service import K8sService

# Per-thread ApiClient to avoid urllib3 connection pool conflicts when
# call_api(_preload_content=False) is used from multiple threads simultaneously.
_thread_local = threading.local()


def _get_thread_api_client() -> Any:
    if not hasattr(_thread_local, "api_client"):
        cfg = k8s_client.Configuration()
        k8s_config.load_kube_config(
            config_file=settings.kubeconfig_path,
            client_configuration=cfg,
        )
        _thread_local.api_client = k8s_client.ApiClient(configuration=cfg)
    return _thread_local.api_client

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


# Marker for a transient exec-handshake failure (K8s API flow-control returns 200
# instead of 101 under load). Callers map this to "unknown", not a hard error: the
# link being checked is fine, the diagnostics exec just couldn't open this tick.
_EXEC_BUSY = "exec unavailable (Kubernetes API busy, transient)"


def _exec(core, pod: str, container: str, cmd: list[str]) -> str:
    # Retry transient WebSocket handshake failures with a short backoff before
    # giving up (a brief pause usually resolves the 200-not-101 handshake).
    for attempt in range(3):
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
            if "Handshake status" in msg:
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                return f"ERROR: {_EXEC_BUSY}"
            return f"ERROR: {msg[:160]}"
    return f"ERROR: {_EXEC_BUSY}"


def _find_pod(core, app: str) -> tuple[str, str] | None:
    """Return (pod_name, container_name) for the first Running pod matching label app=<app>."""
    pods = core.list_namespaced_pod(namespace=NS, label_selector=f"app={app}")
    for p in pods.items:
        if p.metadata.deletion_timestamp:
            continue
        if p.status.phase == "Running":
            return p.metadata.name, app
    return None


def _nf_api_get(core, app: str, port: int, path: str) -> dict[str, Any]:
    """Call NF management HTTP endpoint via K8s API server pod proxy.
    Uses a per-thread ApiClient to avoid urllib3 pool conflicts in ThreadPoolExecutor."""
    try:
        pods = core.list_namespaced_pod(namespace=NS, label_selector=f"app={app}")
        running = [p for p in pods.items if p.status.phase == "Running"
                   and not p.metadata.deletion_timestamp]
        if not running:
            return {}
        pod_name = running[0].metadata.name
        parts = path.split("?", 1)
        api_path = f"/api/v1/namespaces/{NS}/pods/{pod_name}:{port}/proxy/{parts[0]}"
        query_params: list[tuple[str, str]] = []
        if len(parts) > 1:
            for kv in parts[1].split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    query_params.append((k, v))
        api_client = _get_thread_api_client()
        resp = api_client.call_api(
            api_path, "GET",
            query_params=query_params,
            header_params={"Accept": "application/json"},
            auth_settings=[],
            _preload_content=False,
            _return_http_data_only=True,
        )
        return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        log.debug("_nf_api_get %s/%s: %s", app, path, exc)
        return {}





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
        # N2 check via AMF gnb-info (K8s API proxy, thread-safe client).
        # A connected gNB with setup_success=true confirms N2/NGAP is operational.
        data = _nf_api_get(self.k8s.core, "amf", 9090, "gnb-info")
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
        pod_info = _find_pod(self.k8s.core, "netshoot")
        if not pod_info:
            result["status"] = "error"
            result["detail"] = "netshoot pod not running"
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
            result["status"] = "unknown" if _EXEC_BUSY in out else "error"
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
        data = _nf_api_get(self.k8s.core, "smf", 9090, "pdu-info?page=0&page_size=1")
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
        pod_info = _find_pod(self.k8s.core, "netshoot")
        if not pod_info:
            result["status"] = "error"
            result["detail"] = "netshoot pod not running"
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
            # A transient exec-handshake hiccup is not an N6 fault: report unknown.
            result["status"] = "unknown" if _EXEC_BUSY in out else "error"
            result["detail"] = out[:200]
        else:
            result["status"] = "fail"
            result["detail"] = f"N6 gateway {ips['n6c_gw']} unreachable"

        return result

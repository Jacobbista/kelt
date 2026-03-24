"""UE & PDU-session monitoring service.

Combines Prometheus metrics (gauges/counters from AMF/SMF) with K8s log
parsing to provide per-UE visibility.
"""

import json
import logging
import re
import subprocess
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.services.k8s_service import K8sService
from app.services.prometheus_service import PrometheusService

log = logging.getLogger(__name__)

NS = "5g"

# ── AMF log patterns (Open5GS format with ANSI color codes) ────
_RE_REG_REQ = re.compile(
    r"Registration request",
)
_RE_REG_OK = re.compile(
    r"\[imsi-(\d+)\].*Registration complete",
)
_RE_REG_FAIL = re.compile(
    r"\[imsi-(\d+)\].*(?:Registration reject|de-?registered)", re.I,
)
_RE_GNB_ADDED = re.compile(
    r"gNB-N2 accepted\[([^\]]+)\]",
)
_RE_GNB_UE_ADDED = re.compile(
    r"\[Added\] Number of gNB-UEs is now (\d+)",
)
_RE_AMF_UE_ADDED = re.compile(
    r"\[Added\] Number of AMF-UEs is now (\d+)",
)
_RE_AMF_SESSION_ADDED = re.compile(
    r"\[Added\] Number of AMF-Sessions is now (\d+)",
)
_RE_AMF_UE_SUPI = re.compile(
    r"UE SUPI\[imsi-(\d+)\].*DNN\[([^\]]*)\].*S_NSSAI\[([^\]]*)\]",
)

# ── SMF log patterns ────────────────────────────────────────────
_RE_SMF_PDU_EST = re.compile(
    r"UE SUPI\[imsi-(\d+)\]\s+DNN\[([^\]]*)\]\s+IPv4\[([^\]]*)\]",
)
_RE_SMF_SESSION_ADDED = re.compile(
    r"\[Added\] Number of SMF-Sessions is now (\d+)",
)
_RE_SMF_SESSION_REMOVED = re.compile(
    r"\[Removed\] Number of SMF-Sessions is now (\d+)",
)
# Open5GS: "Removed Session: UE IMSI:[imsi-001010123456789] DNN:[internet] IPv4:[10.45.0.2]"
_RE_SMF_REMOVED_SESSION = re.compile(
    r"Removed Session:.*?(?:imsi-|IMSI:\[(?:imsi-)?)(\d+)\]?.*?DNN\[([^\]]*)\].*?IPv4\[([^\]]*)\]",
    re.I,
)
_RE_SMF_UE_ADDED = re.compile(
    r"\[Added\] Number of SMF-UEs is now (\d+)",
)
_RE_SMF_UE_REMOVED = re.compile(
    r"\[Removed\] Number of SMF-UEs is now (\d+)",
)

# ── AMF disconnect / failure patterns ─────────────────────────
_RE_GNB_UE_REMOVED = re.compile(
    r"\[Removed\] Number of gNB-UEs is now (\d+)",
)
_RE_AMF_UE_REMOVED = re.compile(
    r"\[Removed\] Number of AMF-UEs is now (\d+)",
)
_RE_AMF_SESSION_REMOVED = re.compile(
    r"\[Removed\] Number of AMF-Sessions is now (\d+)",
)
_RE_UE_CONTEXT_RELEASE = re.compile(
    r"\[imsi-(\d+)\].*(?:UE Context Release|context released)", re.I,
)
_RE_AUTH_REJECT = re.compile(
    r"\[imsi-(\d+)\].*(?:Authentication reject|authentication failure|auth[- ]?reject)", re.I,
)

# ── Auth failure reason mapping ───────────────────────────────
_AUTH_FAIL_REASONS: dict[str, str] = {
    "mac failure": "SIM auth failed (MAC mismatch — check K/OPc keys)",
    "mac": "SIM auth failed (MAC mismatch — check K/OPc keys)",
    "sqn failure": "Sequence number out of sync (re-provision subscriber)",
    "sqn": "Sequence number out of sync (re-provision subscriber)",
    "unknown": "Subscriber not found in database",
    "not found": "Subscriber not found in database",
    "serving network": "Serving network name mismatch",
}


def _extract_auth_reason(text: str) -> str:
    """Map AMF log text to a human-readable auth failure explanation."""
    lower = text.lower()
    for pattern, explanation in _AUTH_FAIL_REASONS.items():
        if pattern in lower:
            return explanation
    return "Check AMF logs for detailed cause"


class UEService:
    def __init__(self, k8s: K8sService, prom: PrometheusService) -> None:
        self.k8s = k8s
        self.prom = prom

    # ── Prometheus summary ──────────────────────────────────────

    async def get_summary(self) -> dict[str, Any]:
        """Real-time gauge + counter snapshot from AMF/SMF Prometheus."""
        queries = {
            "connected_gnbs": "gnb",
            "ran_ues": "ran_ue",
            "amf_sessions": "amf_session",
            "registered_subscribers": "fivegs_amffunction_rm_registeredsubnbr",
            "reg_init_req": "fivegs_amffunction_rm_reginitreq",
            "reg_init_succ": "fivegs_amffunction_rm_reginitsucc",
            "reg_init_fail": "fivegs_amffunction_rm_reginitfail",
            "reg_mobility_req": "fivegs_amffunction_rm_regmobreq",
            "reg_mobility_succ": "fivegs_amffunction_rm_regmobsucc",
            "reg_periodic_req": "fivegs_amffunction_rm_regperiodreq",
            "auth_req": "fivegs_amffunction_amf_authreq",
            "auth_reject": "fivegs_amffunction_amf_authreject",
            "auth_fail": "fivegs_amffunction_amf_authfail",
            "paging_req": "fivegs_amffunction_mm_paging5greq",
        }
        result: dict[str, float] = {}
        for key, q in queries.items():
            try:
                data = await self.prom.instant_query(q)
                vec = data.get("result", [])
                result[key] = float(vec[0]["value"][1]) if vec else 0
            except Exception:
                result[key] = 0
        return result

    # ── Log-based event parsing ─────────────────────────────────

    def get_events(self, minutes: int = 10, tail: int = 500) -> list[dict[str, Any]]:
        """Parse recent AMF + SMF logs for UE-related events."""
        events: list[dict[str, Any]] = []
        events.extend(self._parse_amf_logs(tail))
        events.extend(self._parse_smf_logs(tail))
        events.sort(key=lambda e: e.get("ts", ""), reverse=True)
        return _deduplicate_events(events)

    def _read_deploy_logs(self, deploy: str, tail: int = 500) -> str:
        try:
            pods = self.k8s.core.list_namespaced_pod(
                namespace=NS, label_selector=f"app={deploy}",
            )
            if not pods.items:
                return ""
            pod = pods.items[0]
            return self.k8s.core.read_namespaced_pod_log(
                name=pod.metadata.name,
                namespace=NS,
                container=deploy,
                tail_lines=tail,
                timestamps=True,
            ) or ""
        except Exception as exc:
            log.debug("Failed to read %s logs: %s", deploy, exc)
            return ""

    def _parse_amf_logs(self, tail: int) -> list[dict[str, Any]]:
        raw = self._read_deploy_logs("amf", tail)
        events: list[dict[str, Any]] = []
        for line in raw.splitlines():
            ts = _extract_ts(line)
            text = _strip_ansi(line)

            m = _RE_GNB_ADDED.search(text)
            if m:
                events.append({"ts": ts, "type": "gnb_connect", "source": "amf",
                               "severity": "info", "detail": f"gNB connected from {m.group(1)}",
                               "gnb_ip": m.group(1)})
                continue

            m = _RE_REG_OK.search(text)
            if m:
                events.append({"ts": ts, "type": "registration_ok", "source": "amf",
                               "severity": "info", "imsi": m.group(1),
                               "detail": f"UE {m.group(1)} registered"})
                continue

            m = _RE_REG_FAIL.search(text)
            if m:
                detail = f"UE {m.group(1)} registration failed"
                reason = "Network rejected registration — check subscriber provisioning"
                if "de-register" in text.lower() or "deregister" in text.lower():
                    detail = f"UE {m.group(1)} de-registered"
                    reason = "UE initiated deregistration (normal shutdown or SIM removal)"
                events.append({"ts": ts, "type": "registration_fail", "source": "amf",
                               "severity": "warning", "imsi": m.group(1),
                               "detail": detail, "reason": reason})
                continue

            if _RE_REG_REQ.search(text):
                events.append({"ts": ts, "type": "registration_req", "source": "amf",
                               "severity": "info",
                               "detail": "Registration request received"})
                continue

            m = _RE_GNB_UE_ADDED.search(text)
            if m:
                events.append({"ts": ts, "type": "ue_count_change", "source": "amf",
                               "severity": "info",
                               "detail": f"gNB-UE count now {m.group(1)}"})
                continue

            m = _RE_AMF_UE_ADDED.search(text)
            if m:
                events.append({"ts": ts, "type": "ue_count_change", "source": "amf",
                               "severity": "info",
                               "detail": f"AMF-UE count now {m.group(1)}"})
                continue

            m = _RE_AMF_SESSION_ADDED.search(text)
            if m:
                events.append({"ts": ts, "type": "session_count_change", "source": "amf",
                               "severity": "info",
                               "detail": f"AMF session count now {m.group(1)}"})
                continue

            m = _RE_AMF_UE_SUPI.search(text)
            if m:
                events.append({"ts": ts, "type": "pdu_request", "source": "amf",
                               "severity": "info", "imsi": m.group(1),
                               "dnn": m.group(2), "slice": m.group(3),
                               "detail": f"UE {m.group(1)} PDU request DNN:{m.group(2)} S-NSSAI:{m.group(3)}"})
                continue

            m = _RE_GNB_UE_REMOVED.search(text)
            if m:
                events.append({"ts": ts, "type": "ue_count_change", "source": "amf",
                               "severity": "warning",
                               "detail": f"gNB-UE count decreased to {m.group(1)}",
                               "reason": "A UE detached from the gNB — may indicate UE power-off or signal loss"})
                continue

            m = _RE_AMF_UE_REMOVED.search(text)
            if m:
                events.append({"ts": ts, "type": "ue_count_change", "source": "amf",
                               "severity": "warning",
                               "detail": f"AMF-UE count decreased to {m.group(1)}",
                               "reason": "UE deregistered from AMF — normal shutdown or network-initiated release"})
                continue

            m = _RE_AMF_SESSION_REMOVED.search(text)
            if m:
                events.append({"ts": ts, "type": "session_count_change", "source": "amf",
                               "severity": "warning",
                               "detail": f"AMF session count decreased to {m.group(1)}",
                               "reason": "An AMF session ended — UE disconnect or idle timeout"})
                continue

            m = _RE_UE_CONTEXT_RELEASE.search(text)
            if m:
                events.append({"ts": ts, "type": "ue_context_release", "source": "amf",
                               "severity": "warning", "imsi": m.group(1),
                               "detail": f"UE {m.group(1)} context released (detached)",
                               "reason": "UE detached from network — power-off, signal loss, or idle timeout"})
                continue

            m = _RE_AUTH_REJECT.search(text)
            if m:
                reason = _extract_auth_reason(text)
                events.append({"ts": ts, "type": "auth_reject", "source": "amf",
                               "severity": "error", "imsi": m.group(1),
                               "detail": f"Authentication rejected for {m.group(1)}",
                               "reason": reason})
                continue

        return events

    def _parse_smf_logs(self, tail: int) -> list[dict[str, Any]]:
        raw = self._read_deploy_logs("smf", tail)
        events: list[dict[str, Any]] = []
        for line in raw.splitlines():
            ts = _extract_ts(line)
            text = _strip_ansi(line)

            m = _RE_SMF_PDU_EST.search(text)
            if m:
                events.append({"ts": ts, "type": "pdu_session_est", "source": "smf",
                               "severity": "info", "imsi": m.group(1),
                               "dnn": m.group(2), "ue_ip": m.group(3),
                               "detail": f"PDU session: {m.group(1)} -> {m.group(3)} (DNN: {m.group(2)})"})
                continue

            m = _RE_SMF_REMOVED_SESSION.search(text)
            if m:
                events.append({"ts": ts, "type": "pdu_session_rel", "source": "smf",
                               "severity": "info", "imsi": m.group(1),
                               "dnn": m.group(2), "ue_ip": m.group(3),
                               "detail": f"PDU session released: {m.group(1)} {m.group(3)} (DNN: {m.group(2)})",
                               "reason": "PDU session ended — UE disconnect or session timeout"})
                continue

            m = _RE_SMF_SESSION_REMOVED.search(text)
            if m:
                events.append({"ts": ts, "type": "session_count_change", "source": "smf",
                               "severity": "info",
                               "detail": f"SMF session count now {m.group(1)}"})
                continue

            m = _RE_SMF_SESSION_ADDED.search(text)
            if m:
                events.append({"ts": ts, "type": "session_count_change", "source": "smf",
                               "severity": "info",
                               "detail": f"SMF session count now {m.group(1)}"})
                continue

            m = _RE_SMF_UE_ADDED.search(text)
            if m:
                events.append({"ts": ts, "type": "ue_count_change", "source": "smf",
                               "severity": "info",
                               "detail": f"SMF-UE count now {m.group(1)}"})
                continue

            m = _RE_SMF_UE_REMOVED.search(text)
            if m:
                events.append({"ts": ts, "type": "ue_count_change", "source": "smf",
                               "severity": "warning",
                               "detail": f"SMF-UE count decreased to {m.group(1)}",
                               "reason": "A UE was removed from SMF — session cleanup"})
        return events

    # ── Active UE list ──────────────────────────────────────────

    async def get_active_ues(self) -> list[dict[str, Any]]:
        """Build an active-UE list by cross-referencing log events.

        Since Open5GS stores sessions in memory (not MongoDB), we reconstruct
        state from recent AMF/SMF log entries.  Events are merged and sorted
        chronologically so that deregistration correctly clears prior sessions.
        Cross-checks with Prometheus ran_ue gauge to detect stale entries.
        """
        # Merge AMF + SMF events and sort chronologically
        all_events = self._parse_amf_logs(tail=1000) + self._parse_smf_logs(tail=1000)
        all_events.sort(key=lambda e: e.get("ts", ""))

        registered: dict[str, dict[str, Any]] = {}
        for ev in all_events:
            imsi = ev.get("imsi")
            if not imsi:
                continue

            if ev["type"] == "registration_ok":
                entry = registered.setdefault(imsi, {"imsi": imsi, "status": "registered",
                                                      "last_seen": ev["ts"], "sessions": []})
                entry["status"] = "registered"
                entry["last_seen"] = ev["ts"]
                # New registration clears stale sessions from previous lifecycle
                entry["sessions"] = []

            elif ev["type"] == "registration_fail":
                entry = registered.setdefault(imsi, {"imsi": imsi, "status": "deregistered",
                                                      "last_seen": ev["ts"], "sessions": []})
                entry["status"] = "deregistered"
                entry["last_seen"] = ev["ts"]
                entry["sessions"] = []

            elif ev["type"] == "ue_context_release":
                entry = registered.setdefault(imsi, {"imsi": imsi, "status": "released",
                                                      "last_seen": ev["ts"], "sessions": []})
                entry["status"] = "released"
                entry["sessions"] = []
                entry["last_seen"] = ev["ts"]

            elif ev["type"] == "pdu_session_est":
                entry = registered.setdefault(imsi, {"imsi": imsi, "status": "registered",
                                                      "last_seen": ev["ts"], "sessions": []})
                ue_ip, dnn = ev.get("ue_ip", ""), ev.get("dnn", "")
                # Avoid duplicate session entries (same IP+DNN)
                if not any(s.get("ue_ip") == ue_ip and s.get("dnn") == dnn for s in entry["sessions"]):
                    entry["sessions"].append({"dnn": dnn, "ue_ip": ue_ip, "ts": ev["ts"]})
                entry["last_seen"] = ev["ts"]

            elif ev["type"] == "pdu_session_rel" and ev.get("ue_ip"):
                ue_ip_rel = ev["ue_ip"]
                dnn_rel = (ev.get("dnn") or "").split(":")[0]
                if imsi in registered:
                    sessions = registered[imsi]["sessions"]
                    for i in range(len(sessions) - 1, -1, -1):
                        s_dnn = (sessions[i].get("dnn") or "").split(":")[0]
                        ip_match = sessions[i].get("ue_ip") == ue_ip_rel
                        dnn_match = not dnn_rel or s_dnn == dnn_rel or dnn_rel in s_dnn or s_dnn in dnn_rel
                        if ip_match and dnn_match:
                            sessions.pop(i)
                            break
                    registered[imsi]["last_seen"] = ev["ts"]

        # Safety net: force-clear sessions for terminal UEs
        for u in registered.values():
            if u["status"] in ("deregistered", "released"):
                u["sessions"] = []

        # Filter stale UEs: drop if last_seen older than 10 minutes
        now = datetime.now(timezone.utc)
        max_age_sec = 10 * 60
        filtered = []
        for u in registered.values():
            try:
                ts = u.get("last_seen", "")
                if not ts:
                    continue
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if (now - dt).total_seconds() <= max_age_sec:
                    filtered.append(u)
            except Exception:
                continue  # skip entries with unparseable timestamps

        # Cross-check with Prometheus: if ran_ue=0 but we have "registered" UEs, mark stale
        try:
            data = await self.prom.instant_query("ran_ue")
            vec = data.get("result", [])
            prom_count = int(float(vec[0]["value"][1])) if vec else 0
            if prom_count == 0:
                for u in filtered:
                    if u["status"] == "registered":
                        u["status"] = "stale"
        except Exception:
            pass

        return sorted(filtered, key=lambda u: u.get("last_seen", ""), reverse=True)

    # ── Open5GS infoAPI: connected gNBs (gnb_id, plmn, peer) ──────

    def get_gnb_info(self) -> list[dict[str, Any]]:
        """Fetch connected gNBs from AMF infoAPI (gnb_id, plmn, peer, num_connected_ues).
        Requires Open5GS built with infoAPI (main branch). Returns [] on failure."""
        try:
            pods = self.k8s.core.list_namespaced_pod(
                namespace=NS, label_selector="app=amf",
            )
            if not pods.items:
                return []
            pod_name = pods.items[0].metadata.name
            cmd = ["sh", "-c", "curl -s -m 5 http://127.0.0.1:9090/gnb-info 2>/dev/null || wget -qO- --timeout=5 http://127.0.0.1:9090/gnb-info 2>/dev/null || echo '{}'"]
            result = self._exec_in_pod(pod_name, cmd)
            if result.get("exit_code") != 0 or not result.get("stdout"):
                return []
            data = json.loads(result["stdout"])
            items = data.get("items", data.get("gnbs", []))
            if not isinstance(items, list):
                return []
            out = []
            for g in items:
                peer = ""
                if isinstance(g.get("ng"), dict) and isinstance(g["ng"].get("sctp"), dict):
                    peer = g["ng"]["sctp"].get("peer", "")
                out.append({
                    "gnb_id": g.get("gnb_id"),
                    "plmn": g.get("plmn", ""),
                    "peer": peer,
                    "num_connected_ues": g.get("num_connected_ues", 0),
                })
            return out
        except Exception as exc:
            log.debug("get_gnb_info failed: %s", exc)
            return []

    # ── UERANSIM UE pods ────────────────────────────────────────

    def get_ue_pods(self) -> list[dict[str, Any]]:
        """List UERANSIM UE pods if deployed."""
        try:
            pods = self.k8s.core.list_namespaced_pod(
                namespace=NS, label_selector="app=ue",
            )
        except Exception:
            pods = type("", (), {"items": []})()
        result = []
        for p in pods.items:
            result.append({
                "name": p.metadata.name,
                "phase": p.status.phase,
                "node": p.spec.node_name,
                "ip": p.status.pod_ip,
            })
        return result

    # ── Connectivity tests ──────────────────────────────────────

    def run_ping(self, pod: str, target: str = "8.8.8.8", count: int = 4) -> dict[str, Any]:
        """Run ping from a UERANSIM UE pod through its uesimtun0 interface."""
        cmd = ["ping", "-c", str(count), "-I", "uesimtun0", "-W", "2", target]
        return self._exec_in_pod(pod, cmd)

    def run_iperf(self, pod: str, server: str = "10.45.0.1", duration: int = 5) -> dict[str, Any]:
        """Run iperf3 client from a UERANSIM UE pod."""
        cmd = ["iperf3", "-c", server, "-t", str(duration), "-J"]
        result = self._exec_in_pod(pod, cmd)
        if result.get("exit_code") == 0:
            try:
                result["parsed"] = json.loads(result["stdout"])
            except (json.JSONDecodeError, KeyError):
                pass
        return result

    def _exec_in_pod(self, pod: str, cmd: list[str]) -> dict[str, Any]:
        from kubernetes.stream import stream
        try:
            resp = stream(
                self.k8s.core.connect_get_namespaced_pod_exec,
                pod, NS, command=cmd,
                stderr=True, stdout=True, stdin=False, tty=False,
            )
            return {"exit_code": 0, "stdout": resp}
        except Exception as exc:
            return {"exit_code": 1, "stdout": "", "stderr": str(exc)}


_RE_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _normalize_gnb_ip(ip: str) -> str:
    """Strip port from IP for deduplication (192.168.6.101:38472 -> 192.168.6.101)."""
    if ":" in ip and not ip.startswith("["):
        return ip.split(":")[0]
    return ip


def _deduplicate_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate events (events are sorted newest-first, so first seen wins).

    Strategy per type:
    - gnb_connect: keep latest per IP
    - registration_ok/fail, auth_reject, ue_context_release: keep latest per IMSI
    - ue_count_change, session_count_change: keep latest per (source, type)
    - pdu_session_est: keep latest per (imsi, ue_ip, dnn)
    - Others: keep all
    """
    seen: dict[str, set[str]] = {}
    out: list[dict[str, Any]] = []
    for ev in events:
        etype = ev.get("type", "")

        if etype == "gnb_connect":
            key = _normalize_gnb_ip(ev.get("gnb_ip", ""))
        elif etype in ("registration_ok", "registration_fail", "auth_reject", "ue_context_release"):
            key = ev.get("imsi", "")
        elif etype in ("ue_count_change", "session_count_change"):
            key = ev.get("source", "")
        elif etype == "pdu_session_est":
            key = f"{ev.get('imsi', '')}:{ev.get('ue_ip', '')}:{ev.get('dnn', '')}"
        else:
            out.append(ev)
            continue

        bucket = seen.setdefault(etype, set())
        if key and key in bucket:
            continue
        if key:
            bucket.add(key)
        out.append(ev)

    return out


def _strip_ansi(text: str) -> str:
    return _RE_ANSI.sub("", text)


_RE_K8S_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)")


def _extract_ts(line: str) -> str:
    """Extract the leading K8s timestamp from a log line (RFC3339 format)."""
    m = _RE_K8S_TS.match(line)
    return m.group(1) if m else ""


def get_ue_service(k8s: K8sService, prom: PrometheusService) -> UEService:
    return UEService(k8s, prom)

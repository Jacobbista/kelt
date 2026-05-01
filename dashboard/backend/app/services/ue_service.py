"""UE & PDU-session monitoring service.

Combines Prometheus metrics (gauges/counters from AMF/SMF) with K8s log
parsing to provide per-UE visibility.
"""

import json
import logging
import re
import subprocess
from typing import Any

from app.config import settings
from app.services.k8s_service import K8sService
from app.services.prometheus_service import PrometheusService

log = logging.getLogger(__name__)

NS = "5g"


def _parse_imsi(imsi: str) -> dict[str, Any]:
    """Split an IMSI into MCC / MNC / MSIN.

    Uses a 2-digit MNC by default (3GPP networks outside North America). No
    operator-name lookup: in a private research testbed the PLMNs are synthetic
    (typically 001-01 or 999-*) and a real-world MNC table would be misleading.
    Consumers can use ``is_test_plmn`` to highlight reserved ranges.
    """
    if not imsi or len(imsi) < 6 or not imsi.isdigit():
        return {"raw": imsi}
    mcc = imsi[:3]
    # MNC length depends on the PLMN; 3GPP allows 2 or 3 digits. Most networks
    # use 2 digits, North America uses 3. We don't have a reliable MCC→MNC-len
    # map, so we default to 2 but expose the raw 3-digit candidate as well.
    mnc2 = imsi[3:5]
    msin = imsi[5:]
    is_test = mcc == "001" or mcc == "999"
    return {
        "imsi": imsi,
        "mcc": mcc,
        "mnc": mnc2,
        "msin": msin,
        "is_test_plmn": is_test,
    }


def _summarize_subscriber(doc: dict[str, Any]) -> dict[str, Any]:
    """Project a subscriber document down to the fields the UE monitor shows.

    Keeps the shape stable even if Open5GS extends the schema, and converts
    AMBR to kbps so the frontend doesn't replicate the 3GPP unit table.
    """
    def _ambr_to_kbps(ambr: Any) -> dict[str, int] | None:
        if not isinstance(ambr, dict):
            return None
        # Open5GS stores AMBR as {value, unit} where unit 1=Kbps, 2=Mbps,
        # 3=Gbps, 4=Tbps (3GPP TS 29.274 "Bit Rate Unit").
        unit_mult = {1: 1, 2: 1000, 3: 1_000_000, 4: 1_000_000_000}
        out: dict[str, int] = {}
        for direction in ("uplink", "downlink"):
            d = ambr.get(direction) or {}
            value = d.get("value")
            unit = d.get("unit")
            if isinstance(value, (int, float)) and unit in unit_mult:
                out[direction] = int(value * unit_mult[unit])
        return out or None

    slices_out: list[dict[str, Any]] = []
    for sl in doc.get("slice") or []:
        if not isinstance(sl, dict):
            continue
        sessions = sl.get("session") or []
        dnns: list[str] = []
        if isinstance(sessions, list):
            dnns = [s.get("name") for s in sessions if isinstance(s, dict) and s.get("name")]
        slices_out.append({
            "sst": sl.get("sst"),
            "sd": sl.get("sd"),
            "default": bool(sl.get("default_indicator")),
            "dnns": dnns,
        })

    security = doc.get("security") or {}
    auth_method = None
    if security.get("opc"):
        auth_method = "OPc"
    elif security.get("op"):
        auth_method = "OP"

    return {
        "configured": True,
        "slices": slices_out,
        "ue_ambr_kbps": _ambr_to_kbps(doc.get("ambr")),
        "auth_method": auth_method,
    }

# ── AMF log patterns (Open5GS format with ANSI color codes) ────
# Registration request: Open5GS usually prefixes with a SUCI or IMSI tag.
# The identifier is optional — a UE may issue a request before the AMF has
# allocated one, in which case only the action is logged.
_RE_REG_REQ = re.compile(
    r"(?:\[(?P<id>suci-[^\]]+|imsi-\d+)\][^\n]*?)?Registration request",
)
_RE_REG_OK = re.compile(
    r"\[imsi-(\d+)\].*Registration complete",
)
# Narrow match: only "Registration reject" (a real failure from the network).
# Deregistration events are handled by dedicated patterns below and must NOT
# collapse into registration_fail (see EVENT_FILTERS in the UI).
_RE_REG_FAIL = re.compile(
    r"\[(?P<id>suci-[^\]]+|imsi-\d+)\].*Registration reject", re.I,
)
# Deregistration: UE-initiated or network-initiated.
# Open5GS logs e.g.:
#   "[imsi-001010000000001] UE-initiated De-registration"
#   "[imsi-001010000000001] Deregistration request"
#   "[imsi-001010000000001] Deregistration accept"
_RE_DEREG_REQ = re.compile(
    r"\[imsi-(\d+)\].*(?:UE-initiated de-?registration|Deregistration request)", re.I,
)
_RE_DEREG_OK = re.compile(
    r"\[imsi-(\d+)\].*Deregistration accept", re.I,
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
# Open5GS emits per-IMSI release lines in several variants:
#   "[imsi-XXX] UE Context Release Request"
#   "[imsi-XXX] UE Context Release Complete"
#   "[imsi-XXX] Release context reason:Radio Connection With UE Lost"
#   "[imsi-XXX] UE-Context-Release-Complete"
# Only the first two matched before, so releases triggered by the RAN dropping
# the UE (signal loss, hard crash) were silently missed and the state machine
# left the UE "registered" indefinitely. Widen the match.
_RE_UE_CONTEXT_RELEASE = re.compile(
    r"\[imsi-(\d+)\].*"
    r"(?:UE[- ]Context[- ]Release|context released|Release\s+context|ngap.*release)",
    re.I,
)
# "Release context reason:<cause>" carries a useful explanation we expose
# verbatim in the UI when available.
_RE_RELEASE_REASON = re.compile(
    r"Release\s+context\s+reason\s*[:=]?\s*([^\n\r,]+)",
    re.I,
)

# Matches auth reject/failure lines.
# Open5GS uses SUCI (not IMSI) at auth phase: "[suci-0-001-01-0-0-0-MSIN]"
# Three forms observed:
#   "[suci-...] Authentication failure [20]"
#   "Authentication failure(MAC failure)"   ← no identifier on this line
#   "[suci-...] Authentication reject"
# Also handles legacy [imsi-X] format as fallback.
_RE_AUTH_REJECT = re.compile(
    r"(?:\[(?P<id1>suci-[^\]]+|imsi-\d+)\][^\n]*\bauth(?:entication)?\b[^\n]*\b(?:reject|failure|fail)\b"
    r"|\bauth(?:entication)?\b[^\n]*\b(?:reject|failure|fail)\b[^\n]*\[(?P<id2>suci-[^\]]+|imsi-\d+)\]"
    r"|\bauth(?:entication)?\b[^\n]*\b(?:reject|failure|fail)\b)",  # no-identifier fallback
    re.I,
)

# Open5GS logs cause code as "Authentication failure [20]" (no "cause" prefix).
# Also handles the legacy "cause[20]" / "cause(20)" form.
_RE_CAUSE_CODE = re.compile(
    r"\bauth(?:entication)?\s+(?:reject|failure|fail)\s*\[(\d+)\]"
    r"|\bcause[\[\(](\d+)[\]\)]",
    re.I,
)

# SUCI structure: suci-{supi_type}-{mcc}-{mnc}-{ri}-{ps}-{hpki}-{scheme_out}
# When protection scheme (ps) = 0 (null), scheme_out is the MSIN in plaintext.
_RE_SUCI_PARTS = re.compile(
    r"suci-(\d+)-(\d{3})-(\d{2,3})-(\d*)-(\d+)-(\d*)-([^\]\s,]+)"
)


def _suci_to_display(ue_id: str) -> str:
    """Return reconstructed IMSI when SUCI uses null protection scheme, else raw tag."""
    if not ue_id.startswith("suci-"):
        # Already an imsi-XXXXXX tag — strip prefix
        return ue_id.removeprefix("imsi-")
    m = _RE_SUCI_PARTS.match(ue_id)
    if m:
        _, mcc, mnc, _, prot_scheme, _, scheme_out = m.groups()
        if prot_scheme == "0":
            return f"{mcc}{mnc}{scheme_out}"
    return ue_id  # encrypted SUCI — return as-is

# 5GMM cause codes → human-readable descriptions (3GPP TS 24.501 §9.11.3.2)
_5GMM_CAUSES: dict[int, str] = {
    3: "Illegal UE — IMSI not provisioned or subscriber JSON malformed",
    6: "Illegal ME",
    7: "5GS services not allowed",
    11: "PLMN not allowed — MCC/MNC in subscriber does not match network",
    12: "Tracking area not allowed",
    15: "No suitable cells in tracking area",
    20: "MAC failure — K/OPc mismatch (SIM key != database value)",
    21: "Synch failure — SQN out of sync (delete and re-provision subscriber)",
    22: "Congestion",
    24: "Security mode rejected, unspecified",
    26: "Non-5G authentication unacceptable",
    27: "N1 mode not allowed",
    31: "Redirection to EPC required",
    71: "ngKSI already in use",
    90: "Payload was not forwarded",
}

# Keyword fallbacks when no cause code is present
_AUTH_FAIL_REASONS: dict[str, str] = {
    "mac failure": "MAC failure — K/OPc mismatch (SIM key != database value)",
    "mac": "MAC failure — K/OPc mismatch (SIM key != database value)",
    "synch failure": "Synch failure — SQN out of sync (delete and re-provision subscriber)",
    "sqn failure": "Synch failure — SQN out of sync (delete and re-provision subscriber)",
    "sqn": "Synch failure — SQN out of sync (delete and re-provision subscriber)",
    "unknown": "Subscriber not found in database — check IMSI and JSON format",
    "not found": "Subscriber not found in database — check IMSI and JSON format",
    "serving network": "Serving network name mismatch (PLMN/MCC-MNC)",
    "illegal ue": "Illegal UE — subscriber not provisioned or JSON malformed",
}


def _extract_auth_reason(text: str) -> str:
    """Return a human-readable auth failure explanation from AMF log text."""
    m = _RE_CAUSE_CODE.search(text)
    if m:
        # _RE_CAUSE_CODE has two groups; only one will be non-None
        raw = m.group(1) or m.group(2)
        if raw:
            code = int(raw)
            desc = _5GMM_CAUSES.get(code, "unknown cause")
            return f"5GMM Cause [{code}]: {desc}"
    lower = text.lower()
    for pattern, explanation in _AUTH_FAIL_REASONS.items():
        if pattern in lower:
            return explanation
    return "Check AMF logs for detailed cause"


def _raw_excerpt(line: str) -> str:
    """Return the log content after the K8s timestamp prefix (first token), capped at 350 chars."""
    # K8s timestamp looks like "2024-01-01T00:00:00.000000000Z " — skip past the first space
    idx = line.find(" ")
    content = line[idx + 1:].strip() if idx != -1 else line.strip()
    return content[:350]


class UEService:
    def __init__(
        self,
        k8s: K8sService,
        prom: PrometheusService,
        mongo: Any | None = None,
    ) -> None:
        # mongo is typed as Any to avoid a hard import cycle with MongoService
        # (the router wires the real instance); the enrichment is best-effort
        # and silently degrades if Mongo is unavailable.
        self.k8s = k8s
        self.prom = prom
        self.mongo = mongo

    # ── Prometheus summary ──────────────────────────────────────

    # Gauges: instantaneous state, queried as-is.
    _SUMMARY_GAUGES: dict[str, str] = {
        "connected_gnbs": "gnb",
        "ran_ues": "ran_ue",
        "amf_sessions": "amf_session",
        "registered_subscribers": "fivegs_amffunction_rm_registeredsubnbr",
    }
    # Counters: monotonic totals since AMF start. Shown windowed via increase().
    _SUMMARY_COUNTERS: dict[str, str] = {
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

    async def get_summary(self, window_seconds: int = 300) -> dict[str, Any]:
        """Snapshot of AMF/SMF Prometheus state.

        Gauges are queried as instantaneous values. Counters are windowed using
        PromQL ``increase(metric[Ns])`` so the UI displays activity over the
        selected period instead of a monotonic total accumulated since AMF
        process start (which grows indefinitely and is misleading for UX).
        """
        window_seconds = max(60, min(int(window_seconds or 300), 86400))
        result: dict[str, Any] = {
            "_window_seconds": window_seconds,
            "_counter_mode": "increase",
        }
        # Gauges (instant)
        for key, metric in self._SUMMARY_GAUGES.items():
            try:
                data = await self.prom.instant_query(metric)
                vec = data.get("result", [])
                result[key] = float(vec[0]["value"][1]) if vec else 0
            except Exception:
                result[key] = 0
        # Counters (windowed)
        for key, metric in self._SUMMARY_COUNTERS.items():
            try:
                # sum() collapses multiple series (e.g. one per AMF instance)
                # so the tile reflects cluster-wide activity.
                query = f"sum(increase({metric}[{window_seconds}s]))"
                data = await self.prom.instant_query(query)
                vec = data.get("result", [])
                raw = float(vec[0]["value"][1]) if vec else 0.0
                # increase() returns float; round to int for counters.
                result[key] = int(round(raw)) if raw == raw else 0
            except Exception:
                result[key] = 0
        return result

    # ── Log-based event parsing ─────────────────────────────────

    def get_events(self, minutes: int = 10, tail: int = 500) -> list[dict[str, Any]]:
        """Parse recent AMF + SMF logs for UE-related events."""
        since = minutes * 60
        events: list[dict[str, Any]] = []
        events.extend(self._parse_amf_logs(since_seconds=since))
        events.extend(self._parse_smf_logs(since_seconds=since))
        events.sort(key=lambda e: e.get("ts", ""), reverse=True)
        return _deduplicate_events(events)

    def _read_deploy_logs(self, deploy: str, tail: int = 500, since_seconds: int | None = None) -> str:
        try:
            pods = self.k8s.core.list_namespaced_pod(
                namespace=NS, label_selector=f"app={deploy}",
            )
            if not pods.items:
                return ""
            pod = pods.items[0]
            kwargs: dict = dict(
                name=pod.metadata.name,
                namespace=NS,
                container=deploy,
                timestamps=True,
            )
            if since_seconds is not None:
                kwargs["since_seconds"] = since_seconds
            else:
                kwargs["tail_lines"] = tail
            return self.k8s.core.read_namespaced_pod_log(**kwargs) or ""
        except Exception as exc:
            log.debug("Failed to read %s logs: %s", deploy, exc)
            return ""

    def _parse_amf_logs(self, tail: int = 500, since_seconds: int | None = None) -> list[dict[str, Any]]:
        raw = self._read_deploy_logs("amf", tail=tail, since_seconds=since_seconds)
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
                raw_id = m.group("id") or ""
                imsi = _suci_to_display(raw_id) if raw_id else ""
                events.append({
                    "ts": ts, "type": "registration_fail", "source": "amf",
                    "severity": "warning", "imsi": imsi,
                    "detail": f"UE {imsi} registration rejected" if imsi else "UE registration rejected",
                    "reason": "Network rejected Registration Request — check subscriber provisioning",
                })
                continue

            m = _RE_DEREG_OK.search(text)
            if m:
                events.append({
                    "ts": ts, "type": "deregistration_ok", "source": "amf",
                    "severity": "info", "imsi": m.group(1),
                    "detail": f"UE {m.group(1)} deregistration complete",
                    "reason": "UE has been removed from AMF context (normal shutdown, SIM pulled, or network release)",
                })
                continue

            m = _RE_DEREG_REQ.search(text)
            if m:
                events.append({
                    "ts": ts, "type": "deregistration_req", "source": "amf",
                    "severity": "info", "imsi": m.group(1),
                    "detail": f"UE {m.group(1)} deregistration requested",
                    "reason": "UE is disconnecting (power-off, airplane mode, or network-initiated release)",
                })
                continue

            m = _RE_REG_REQ.search(text)
            if m:
                raw_id = m.group("id") or ""
                imsi = _suci_to_display(raw_id) if raw_id else ""
                events.append({
                    "ts": ts, "type": "registration_req", "source": "amf",
                    "severity": "info", "imsi": imsi,
                    "detail": f"Registration request from {imsi}" if imsi else "Registration request received",
                })
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
                reason = "UE detached from network — power-off, signal loss, or idle timeout"
                reason_m = _RE_RELEASE_REASON.search(text)
                if reason_m:
                    # Surface Open5GS's own cause string (e.g. "Radio Connection With UE Lost")
                    reason = f"gNB reported: {reason_m.group(1).strip()}"
                events.append({"ts": ts, "type": "ue_context_release", "source": "amf",
                               "severity": "warning", "imsi": m.group(1),
                               "detail": f"UE {m.group(1)} context released (detached)",
                               "reason": reason})
                continue

            m = _RE_AUTH_REJECT.search(text)
            if m:
                raw_id = m.group("id1") or m.group("id2") or ""
                imsi = _suci_to_display(raw_id) if raw_id else ""
                reason = _extract_auth_reason(text)
                cause_m = _RE_CAUSE_CODE.search(text)
                ev: dict[str, Any] = {
                    "ts": ts, "type": "auth_reject", "source": "amf",
                    "severity": "error", "imsi": imsi,
                    "detail": f"Authentication rejected for {imsi}" if imsi else "Authentication rejected",
                    "reason": reason,
                    "raw_log": _raw_excerpt(text),
                }
                if cause_m:
                    raw_cause = cause_m.group(1) or cause_m.group(2)
                    if raw_cause:
                        ev["cause_code"] = int(raw_cause)
                events.append(ev)
                continue

        return events

    def _parse_smf_logs(self, tail: int = 500, since_seconds: int | None = None) -> list[dict[str, Any]]:
        raw = self._read_deploy_logs("smf", tail=tail, since_seconds=since_seconds)
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
        # Merge AMF + SMF events over a 4-hour window to catch long-connected UEs
        all_events = (
            self._parse_amf_logs(since_seconds=14400)
            + self._parse_smf_logs(since_seconds=14400)
        )
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
                # Network rejected the Registration Request (cause code in log).
                # Treat as "failed" not "deregistered": the UE never made it.
                entry = registered.setdefault(imsi, {"imsi": imsi, "status": "failed",
                                                      "last_seen": ev["ts"], "sessions": []})
                entry["status"] = "failed"
                entry["last_seen"] = ev["ts"]
                entry["sessions"] = []

            elif ev["type"] in ("deregistration_req", "deregistration_ok"):
                # UE is detaching. deregistration_ok is the terminal transition;
                # deregistration_req is intermediate (state becomes "deregistering"
                # until the accept arrives or the context release fires).
                terminal = ev["type"] == "deregistration_ok"
                new_status = "deregistered" if terminal else "deregistering"
                entry = registered.setdefault(imsi, {"imsi": imsi, "status": new_status,
                                                      "last_seen": ev["ts"], "sessions": []})
                entry["status"] = new_status
                entry["last_seen"] = ev["ts"]
                if terminal:
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
                # For a given DNN, the latest IP always replaces the previous one.
                # This prevents stale IPs from old registration cycles accumulating
                # alongside current sessions when the 4-hour log window spans multiple
                # registration cycles for the same UE.
                existing_idx = next(
                    (i for i, s in enumerate(entry["sessions"]) if s.get("dnn") == dnn), -1
                )
                session_entry = {"dnn": dnn, "ue_ip": ue_ip, "ts": ev["ts"]}
                if existing_idx >= 0:
                    entry["sessions"][existing_idx] = session_entry
                else:
                    entry["sessions"].append(session_entry)
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
            if u["status"] in ("deregistered", "released", "failed"):
                u["sessions"] = []

        active = list(registered.values())

        # Attach a default status_reason for terminal/transitional states so
        # the UI can always show a human-readable tooltip on the status badge.
        _DEFAULT_REASONS = {
            "released": "gNB-N2 context released (idle timeout, signal loss, or power-off)",
            "deregistered": "UE completed Deregistration",
            "deregistering": "UE is detaching (Deregistration Request received)",
            "failed": "Registration Request rejected by network",
        }
        for u in active:
            if u["status"] in _DEFAULT_REASONS and "status_reason" not in u:
                u["status_reason"] = _DEFAULT_REASONS[u["status"]]

        # ── Reconcile with Prometheus ran_ue gauge ──────────────────
        # The AMF keeps context for a UE until ue_context_release is fully
        # processed. Hard UE crashes (reboot loops, SIM yanked without
        # deregistration) leave the AMF with an orphan context while the gNB
        # has already dropped the UE. In that state:
        #   - log-based list → N UEs still "registered"
        #   - ran_ue gauge   → fewer UEs actually on-air
        # Trust the gauge: mark the N-oldest registered UEs as stale with an
        # explicit reason. This is the source of truth for "who's live right
        # now" and makes the inconsistency visible in the UI.
        prom_ran_ue: int | None = None
        prom_amf_session: int | None = None
        try:
            data = await self.prom.instant_query("ran_ue")
            vec = data.get("result", [])
            prom_ran_ue = int(float(vec[0]["value"][1])) if vec else 0
        except Exception:
            prom_ran_ue = None
        try:
            data = await self.prom.instant_query("amf_session")
            vec = data.get("result", [])
            prom_amf_session = int(float(vec[0]["value"][1])) if vec else 0
        except Exception:
            prom_amf_session = None

        if prom_ran_ue is not None:
            registered_ues = [u for u in active if u["status"] == "registered"]
            if len(registered_ues) > prom_ran_ue:
                # Oldest last_seen first — those are the most likely to be
                # the ones the RAN has dropped.
                registered_ues.sort(key=lambda u: u.get("last_seen", ""))
                excess = len(registered_ues) - prom_ran_ue
                reason = (
                    f"AMF retains context but gNB reports {prom_ran_ue} UE"
                    f"{'s' if prom_ran_ue != 1 else ''} on-air "
                    f"({len(registered_ues)} in log window). "
                    "Likely hard crash or radio drop without Deregistration."
                )
                for u in registered_ues[:excess]:
                    u["status"] = "stale"
                    u["status_reason"] = reason
                    # Sessions belong to an orphaned AMF context — clear them so
                    # the UI doesn't claim the UE has live PDU sessions.
                    u["sessions"] = []

        # ── Enrichment (best-effort, never blocks the UE list) ──────
        # 1. IMSI decoding — always cheap, no external call.
        for u in active:
            u["plmn"] = _parse_imsi(u.get("imsi", ""))

        # 2. Subscriber config from MongoDB (slice/AMBR/DNNs/auth method).
        # 3. Dashboard-only personalizations (nickname, icon).
        if self.mongo is not None:
            try:
                personalizations = self.mongo.get_ue_personalizations_map()
            except Exception:
                personalizations = {}
            for u in active:
                imsi = u.get("imsi")
                if not imsi:
                    continue
                try:
                    doc = self.mongo.get_subscriber(imsi)
                except Exception:
                    doc = None
                u["subscription"] = (
                    _summarize_subscriber(doc) if doc else {"configured": False}
                )
                pers = personalizations.get(imsi)
                if pers:
                    u["personalization"] = {
                        "nickname": pers.get("nickname"),
                        "icon": pers.get("icon"),
                    }

        # 4. Attach gNB IP only when exactly one gNB is currently connected —
        #    otherwise the mapping IMSI→gNB is ambiguous from logs alone and
        #    showing a best-guess value would be misleading.
        recent_gnb_ips: list[str] = []
        for ev in all_events:
            if ev.get("type") == "gnb_connect":
                ip = _normalize_gnb_ip(ev.get("gnb_ip", ""))
                if ip and ip not in recent_gnb_ips:
                    recent_gnb_ips.append(ip)
        if len(recent_gnb_ips) == 1:
            for u in active:
                u["gnb_ip"] = recent_gnb_ips[0]

        return sorted(active, key=lambda u: u.get("last_seen", ""), reverse=True)

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


# Event types that tend to repeat in rapid bursts during SIM retry loops or
# AMF reconnect storms. They get collapsed into a single feed row with a
# multiplicity counter instead of flooding the UI.
_BURST_EVENT_TYPES = frozenset({
    "registration_req",
    "registration_fail",
    "auth_reject",
    "deregistration_req",
    "deregistration_ok",
    # Crashing UEs re-request PDU sessions in tight loops. Collapse those too.
    "pdu_request",
    "pdu_session_est",
})
_BURST_BUCKET_SECONDS = 30


def _ts_bucket(ts: str, size: int) -> str:
    """Truncate an RFC3339 timestamp to a bucket of ``size`` seconds.

    Returns an empty string when ``ts`` is malformed, which disables bucketing
    for that event (so it is never accidentally collapsed).
    """
    if not ts or len(ts) < 19:
        return ""
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        epoch = int(dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp())
        return str((epoch // size) * size)
    except (ValueError, TypeError):
        return ""


def _deduplicate_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse noisy repeats while preserving single-shot events.

    Burst types (registration retries, auth rejects, deregistration) are
    bucketed by ``(type, imsi, 30s_bucket)`` and merged: the first occurrence
    carries ``multiplicity``, ``first_ts`` and ``last_ts`` so the UI can render
    a single row with a ``×N`` badge instead of N separate rows.

    Non-burst types fall back to the previous "keep-latest per key" strategy
    so recurring state-snapshots (gNB connected, UE count changes) don't spam
    the feed either. Events are expected newest-first.
    """
    latest_keys: dict[str, set[str]] = {}
    burst: dict[tuple[str, str, str], dict[str, Any]] = {}
    out: list[dict[str, Any]] = []

    for ev in events:
        etype = ev.get("type", "")

        if etype in _BURST_EVENT_TYPES:
            imsi = ev.get("imsi", "")
            bucket = _ts_bucket(ev.get("ts", ""), _BURST_BUCKET_SECONDS)
            bkey = (etype, imsi, bucket)
            if bucket and bkey in burst:
                agg = burst[bkey]
                agg["multiplicity"] = agg.get("multiplicity", 1) + 1
                ev_ts = ev.get("ts", "")
                if ev_ts and ev_ts < agg.get("first_ts", agg.get("ts", "")):
                    agg["first_ts"] = ev_ts
                continue
            enriched = dict(ev)
            enriched["multiplicity"] = 1
            enriched["first_ts"] = ev.get("ts", "")
            enriched["last_ts"] = ev.get("ts", "")
            if bucket:
                burst[bkey] = enriched
            out.append(enriched)
            continue

        if etype == "gnb_connect":
            key = _normalize_gnb_ip(ev.get("gnb_ip", ""))
        elif etype in ("registration_ok", "ue_context_release"):
            key = ev.get("imsi", "")
        elif etype in ("ue_count_change", "session_count_change"):
            key = ev.get("source", "")
        elif etype == "pdu_session_est":
            key = f"{ev.get('imsi', '')}:{ev.get('ue_ip', '')}:{ev.get('dnn', '')}"
        else:
            out.append(ev)
            continue

        seen = latest_keys.setdefault(etype, set())
        if key and key in seen:
            continue
        if key:
            seen.add(key)
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

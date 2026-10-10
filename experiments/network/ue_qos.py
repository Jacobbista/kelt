"""The QoS of the measured UE's PDU session, written into ue.json.

The UE is found by its address in the SMF's /pdu-info (the same management API
the dashboard reads); its UE-AMBR in force comes from the AMF's /ue-info. The
Session-AMBR is not exposed by either API, so it is read from the subscriber
record for the session's slice and DNN, and marked as such. A value that cannot
be found is "unknown". Owner: experiments/README.md.

Usage:
  python3 -m network.ue_qos <ue.json> <key> <address> <pdu-info.json> <ue-info.json> <subscriber.json>
  python3 -m network.ue_qos supi <address> <pdu-info.json>   # the IMSI digits, or nothing
"""
from __future__ import annotations

import json
import os
import sys

UNKNOWN = "unknown"
# Open5GS subscriber bitrate units: value x 1000^unit bit/s.
_MBIT_PER_UNIT = {0: 1e-6, 1: 1e-3, 2: 1, 3: 1e3, 4: 1e6}


def _mbit(bitrate: dict | None) -> float | str:
    try:
        v = bitrate["value"] * _MBIT_PER_UNIT[bitrate["unit"]]
    except (KeyError, TypeError):
        return UNKNOWN
    return int(v) if v == int(v) else v


def _bps_to_mbit(bps) -> float | str:
    if not isinstance(bps, (int, float)):
        return UNKNOWN
    v = bps / 1e6
    return int(v) if v == int(v) else v


def _session(address: str, pdu_info: dict) -> tuple[dict | None, str | None]:
    ip = address.split("/", 1)[0]
    found = (None, None)
    for item in (pdu_info or {}).get("items", []):
        for p in item.get("pdu", []):
            if p.get("ipv4") == ip:
                found = (p, item.get("supi"))
    return found


def supi_of(address: str, pdu_info: dict) -> str:
    """The IMSI digits of the session holding `address`; "" when none, or not digits."""
    _, supi = _session(address, pdu_info)
    imsi = (supi or "").removeprefix("imsi-")
    return imsi if imsi.isdigit() else ""


def ue_qos(address: str, pdu_info: dict, ue_info: dict, subscriber: dict) -> dict:
    out = {k: UNKNOWN for k in ("supi", "dnn", "five_qi", "pdu_state", "ue_ambr_dl_mbit", "ue_ambr_ul_mbit",
                                "session_ambr_dl_mbit", "session_ambr_ul_mbit")}
    out["session_ambr_source"] = "subscriber_record"
    pdu, supi = _session(address, pdu_info)
    if pdu is None:
        return out
    flows = pdu.get("qos_flows") or [{}]
    out.update(supi=supi or UNKNOWN, dnn=pdu.get("dnn", UNKNOWN), pdu_state=pdu.get("pdu_state", UNKNOWN),
               five_qi=flows[0].get("5qi", UNKNOWN))
    for ue in (ue_info or {}).get("items", []):
        if ue.get("supi") == supi:
            ambr = ue.get("ambr") or {}
            out.update(ue_ambr_dl_mbit=_bps_to_mbit(ambr.get("downlink")), ue_ambr_ul_mbit=_bps_to_mbit(ambr.get("uplink")))
    snssai = pdu.get("snssai") or {}
    if subscriber and supi and supi.removeprefix("imsi-") == subscriber.get("imsi"):
        for sl in subscriber.get("slice", []):
            if sl.get("sst") != snssai.get("sst") or sl.get("sd") != snssai.get("sd"):
                continue
            for s in sl.get("session", []):
                if s.get("name") == pdu.get("dnn"):
                    ambr = s.get("ambr") or {}
                    out.update(session_ambr_dl_mbit=_mbit(ambr.get("downlink")),
                               session_ambr_ul_mbit=_mbit(ambr.get("uplink")))
    return out


def _load(path: str) -> dict:
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def main(argv: list[str]) -> None:
    if argv[:1] == ["supi"]:
        print(supi_of(argv[1], _load(argv[2])))
        return
    target, key, address, pdu_path, ue_path, sub_path = argv
    doc = _load(target) if os.path.exists(target) else {}
    doc[key] = ue_qos(address, _load(pdu_path), _load(ue_path), _load(sub_path))
    with open(target, "w") as fh:
        json.dump(doc, fh, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])

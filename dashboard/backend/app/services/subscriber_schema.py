"""Normalize subscriber payloads to match Open5GS schema used by subscriber_import."""

from typing import Any

OPEN5GS_SUBSCRIBER_DEFAULTS: dict[str, Any] = {
    "subscribed_rau_tau_timer": 12,
    "network_access_mode": 0,
    "subscriber_status": 0,
    "access_restriction_data": 32,
    "msisdn": [],
    "schema_version": 1,
    "__v": 0,
}

DEFAULT_OP = "11111111111111111111111111111111"
DEFAULT_AMF = "8000"


def _is_valid_hex(s: str, length: int = 32) -> bool:
    """Check if string is a valid hex string of given length."""
    if not s or not isinstance(s, str):
        return False
    s = s.strip().lower()
    if len(s) != length:
        return False
    try:
        int(s, 16)
        return True
    except ValueError:
        return False


def normalize_subscriber(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Merge payload with Open5GS defaults. Ensures create/update produce
    documents compatible with subscriber_import schema.
    """
    payload = dict(payload)
    payload.pop("_id", None)

    # Top-level defaults (payload overrides)
    result = {**OPEN5GS_SUBSCRIBER_DEFAULTS, **payload}

    # Security block
    security = result.get("security") or {}
    if not isinstance(security, dict):
        security = {}
    security = dict(security)

    if not security.get("amf"):
        security["amf"] = DEFAULT_AMF

    # Normalize empty strings to None
    op = security.get("op")
    if op == "" or (isinstance(op, str) and not op.strip()):
        op = None
    elif op is not None and not _is_valid_hex(str(op)):
        op = None

    opc = security.get("opc")
    if opc == "" or (isinstance(opc, str) and not opc.strip()):
        opc = None
    elif opc is not None and not _is_valid_hex(str(opc)):
        opc = None

    # Enforce mutual exclusivity: if OPc is provided, OP must be null; if OP is provided, OPc must be null.
    if opc is not None:
        op = None
    elif op is not None:
        opc = None

    # op: default if opc is null and op not provided
    if opc is None and not op:
        op = DEFAULT_OP

    security["op"] = op
    security["opc"] = opc

    result["security"] = security

    return result

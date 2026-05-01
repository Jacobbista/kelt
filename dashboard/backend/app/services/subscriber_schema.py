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


class SubscriberSchemaError(ValueError):
    """Raised when a subscriber payload contains invalid security key values."""


def normalize_subscriber(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Merge payload with Open5GS defaults. Ensures create/update produce
    documents compatible with subscriber_import schema.

    Raises SubscriberSchemaError if K, OP, or OPc values are present but malformed,
    rather than silently discarding them (which would produce a subscriber that fails
    authentication with no visible error).
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

    # Validate K (always required — 32 hex chars)
    k = security.get("k")
    if k is not None:
        k = str(k).strip()
        if k and not _is_valid_hex(k, 32):
            raise SubscriberSchemaError(
                f"Invalid K value '{k[:8]}…': must be a 32-character hex string (128-bit key)"
            )

    # Validate OP / OPc — reject malformed values instead of silently dropping
    op = security.get("op")
    if op == "" or (isinstance(op, str) and not op.strip()):
        op = None
    elif op is not None:
        op = str(op).strip()
        if not _is_valid_hex(op, 32):
            raise SubscriberSchemaError(
                f"Invalid OP value '{op[:8]}…': must be a 32-character hex string"
            )

    opc = security.get("opc")
    if opc == "" or (isinstance(opc, str) and not opc.strip()):
        opc = None
    elif opc is not None:
        opc = str(opc).strip()
        if not _is_valid_hex(opc, 32):
            raise SubscriberSchemaError(
                f"Invalid OPc value '{opc[:8]}…': must be a 32-character hex string"
            )

    # Enforce mutual exclusivity: if OPc is provided, OP must be null; if OP is provided, OPc must be null.
    if opc is not None:
        op = None
    elif op is not None:
        opc = None

    # Fall back to default OP only when neither OP nor OPc was given
    if opc is None and not op:
        op = DEFAULT_OP

    security["op"] = op
    security["opc"] = opc

    result["security"] = security

    return result

"""The audit record: who signed in, and who changed what.

Two sources, one list (GET /api/v1/audit, Settings -> Audit):
- Keycloak's own events (sign-ins, failures, admin changes to users, roles and
  clients), stored by Keycloak for audit_retention_days (all.yml, phase 08);
- the dashboard's actions, written here (write_audit) with the user who made the
  request, kept for the same number of days.

The rows are a common shape: ts, who, kind (access | change), action, outcome
(ok | failed), source (keycloak | dashboard), ip, detail.
"""
import json
import os
import tempfile
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

# The user of the current request, set by the middleware in app/main.py before
# the route runs (a FastAPI dependency runs in its own thread, so it could not).
current_actor: ContextVar[str | None] = ContextVar("current_actor", default=None)
# Where the request came from: client ip (as forwarded by Cloudflare and the
# front door), browser, method and path. Set by the same middleware.
current_request: ContextVar[dict[str, Any] | None] = ContextVar("current_request", default=None)

_TRIM_EVERY_S = 86400
_last_trim = 0.0


def trim_lines(lines: list[str], now: datetime, days: int) -> list[str]:
    """Drop the entries older than `days`; keep a line that cannot be read."""
    cutoff = now.timestamp() - days * 86400
    kept = []
    for line in lines:
        try:
            ts = datetime.fromisoformat(json.loads(line)["ts"]).timestamp()
        except (ValueError, KeyError, TypeError):
            kept.append(line)
            continue
        if ts >= cutoff:
            kept.append(line)
    return kept


def _trim(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = trim_lines(lines, datetime.now(timezone.utc), settings.audit_retention_days)
    if len(kept) == len(lines):
        return
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".audit.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("".join(line + "\n" for line in kept))
    os.replace(tmp, path)


def write_audit(event: str, details: dict[str, Any]) -> None:
    global _last_trim
    settings.ensure_audit_dir()
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "actor": current_actor.get(),
        "request": current_request.get(),
        "details": details,
    }
    path = Path(settings.audit_log_path)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    # The retention is applied as the record grows, at most once a day.
    if time.monotonic() - _last_trim > _TRIM_EVERY_S or _last_trim == 0.0:
        _last_trim = time.monotonic()
        _trim(path)


def read_local() -> list[dict[str, Any]]:
    path = Path(settings.audit_log_path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# ── rows ───────────────────────────────────────────────────────────────────

_USER_ACTIONS = {
    "LOGIN": "Signed in",
    "LOGIN_ERROR": "Sign-in failed",
    "LOGOUT": "Signed out",
    "LOGOUT_ERROR": "Sign-out failed",
    "CODE_TO_TOKEN_ERROR": "Sign-in completion failed",
    "REFRESH_TOKEN_ERROR": "Session renewal failed",
    "CLIENT_LOGIN_ERROR": "Client authentication failed",
    "UPDATE_PASSWORD": "Password changed",
    "UPDATE_PASSWORD_ERROR": "Password change failed",
}


# Keycloak event details worth showing; ids of codes and tokens are left out.
_KC_DETAILS = ("reason", "redirect_uri", "auth_type", "auth_method", "grant_type", "identity_provider", "client_auth_method")


def _label(key: str) -> str:
    return key.replace("_", " ")


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def from_user_event(e: dict[str, Any], users: dict[str, str]) -> dict[str, Any]:
    kind = e.get("type", "")
    details = e.get("details") or {}
    # A failed renewal carries no userId: the user is the refresh token's subject.
    uid = e.get("userId") or details.get("refresh_token_sub") or ""
    who = details.get("username") or users.get(uid) or uid or "unknown"
    detail = " · ".join(x for x in (f"client {e['clientId']}" if e.get("clientId") else "", e.get("error") or "") if x)
    return {
        "ts": _iso(e["time"]),
        "who": who,
        "kind": "access",
        "action": _USER_ACTIONS.get(kind, kind.replace("_", " ").capitalize()),
        "outcome": "failed" if kind.endswith("_ERROR") or e.get("error") else "ok",
        "source": "keycloak",
        "ip": e.get("ipAddress"),
        "detail": detail,
        "meta": {
            **({"client": e["clientId"]} if e.get("clientId") else {}),
            **({"session": e["sessionId"]} if e.get("sessionId") else {}),
            **{_label(k): details[k] for k in _KC_DETAILS if details.get(k)},
        },
    }


def from_admin_event(e: dict[str, Any], users: dict[str, str]) -> dict[str, Any]:
    auth = e.get("authDetails") or {}
    op = (e.get("operationType") or "").capitalize()
    res = (e.get("resourceType") or "").replace("_", " ").lower()
    return {
        "ts": _iso(e["time"]),
        "who": users.get(auth.get("userId") or "") or auth.get("userId") or "unknown",
        "kind": "change",
        "action": f"{op} {res}".strip(),
        "outcome": "failed" if e.get("error") else "ok",
        "source": "keycloak",
        "ip": auth.get("ipAddress"),
        "detail": " · ".join(x for x in (e.get("resourcePath") or "", e.get("error") or "") if x),
        "meta": {
            k: v for k, v in (
                ("resource", e.get("resourcePath")),
                ("operation", e.get("operationType")),
                ("by client", auth.get("clientId")),
                ("error", e.get("error")),
            ) if v
        },
    }


def from_local(entry: dict[str, Any]) -> dict[str, Any]:
    details = entry.get("details") or {}
    req = entry.get("request") or {}
    meta = {str(k): v for k, v in details.items()}
    if req.get("method") and req.get("path"):
        meta["request"] = f"{req['method']} {req['path']}"
    if req.get("user_agent"):
        meta["browser"] = req["user_agent"]
    return {
        "ts": entry.get("ts", ""),
        "who": entry.get("actor") or "unknown",
        "kind": "change",
        "action": entry.get("event", ""),
        "outcome": "ok",
        "source": "dashboard",
        "ip": req.get("ip"),
        "detail": " ".join(f"{k}={v}" for k, v in details.items()),
        "meta": meta,
    }


FILTERS = {
    "all": lambda r: True,
    "access": lambda r: r["kind"] == "access",
    "changes": lambda r: r["kind"] == "change",
    "failures": lambda r: r["outcome"] == "failed",
}


def select(rows: list[dict[str, Any]], kind: str, since: datetime, limit: int = 500) -> list[dict[str, Any]]:
    keep = FILTERS[kind]
    cutoff = since.timestamp()
    picked = [r for r in rows if keep(r) and datetime.fromisoformat(r["ts"]).timestamp() >= cutoff]
    picked.sort(key=lambda r: r["ts"], reverse=True)
    return picked[:limit]

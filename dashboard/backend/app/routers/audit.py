"""Settings -> Audit: who signed in, and who changed what (admin only).

Keycloak's events and the dashboard's own record of actions, as one list; see
app/services/audit.py. Keycloak not answering does not hide the dashboard's
entries: the reply says which source is missing.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.services import audit
from app.services.iam_service import IamService

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("")
def list_audit(kind: str = Query("all"), days: int = Query(7, ge=1)) -> dict:
    if kind not in audit.FILTERS:
        raise HTTPException(400, f"kind must be one of {', '.join(audit.FILTERS)}")
    days = min(days, settings.audit_retention_days)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = [audit.from_local(e) for e in audit.read_local()]
    missing = []
    try:
        kc = IamService().audit_events(since.date().isoformat())
        rows += [audit.from_user_event(e, kc["users"]) for e in kc["user_events"]]
        rows += [audit.from_admin_event(e, kc["users"]) for e in kc["admin_events"]]
    except Exception as exc:  # Keycloak down or not configured: show the rest
        missing.append({"source": "keycloak", "error": str(exc)})
    return {
        "rows": audit.select(rows, kind, since),
        "days": days,
        "retention_days": settings.audit_retention_days,
        "missing": missing,
    }

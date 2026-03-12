"""Admin endpoints (restart backend, etc.)."""

import logging
import subprocess
from typing import Any

from fastapi import APIRouter, Header, HTTPException

from app.config import settings
from app.services.audit import write_audit

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
log = logging.getLogger(__name__)


@router.post("/restart-backend")
def restart_backend(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    """
    Restart the dashboard-backend systemd service.
    Requires sudo (vagrant user needs NOPASSWD for systemctl restart).
    This process will be killed by systemd; the response may not be delivered.
    Token optional in lab; set X-Admin-Token if DASHBOARD_ADMIN_TOKEN is configured.
    """
    if settings.admin_token != "change-me" and x_admin_token != settings.admin_token:
        raise HTTPException(status_code=403, detail="Invalid or missing admin token")

    svc = settings.backend_service_name
    log.warning("Restarting backend service: %s", svc)
    write_audit("admin.restart_backend", {"service": svc})

    try:
        subprocess.run(
            ["sudo", "systemctl", "restart", svc],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        log.warning("systemctl restart %s timed out (service may still restart)", svc)
    except FileNotFoundError:
        raise HTTPException(500, "systemctl not found") from None
    except Exception as exc:
        log.exception("Failed to restart %s: %s", svc, exc)
        raise HTTPException(500, detail=str(exc)) from exc

    return {"status": "restarting", "service": svc}

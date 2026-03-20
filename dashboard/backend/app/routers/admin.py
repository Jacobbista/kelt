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


@router.get("/service-status")
def service_status() -> dict[str, Any]:
    """Return systemd service status and recent journal lines for the backend."""
    svc = settings.backend_service_name
    result: dict[str, Any] = {"service": svc}

    # systemctl status (exit code 0=active, 3=inactive/failed)
    try:
        proc = subprocess.run(
            ["systemctl", "status", svc, "--no-pager", "-l"],
            capture_output=True, text=True, timeout=5,
        )
        result["status_output"] = proc.stdout.strip()
        result["active"] = proc.returncode == 0
    except Exception as exc:
        result["status_output"] = f"Error: {exc}"
        result["active"] = False

    # Recent journal entries
    try:
        proc = subprocess.run(
            ["journalctl", "-u", svc, "--no-pager", "-n", "40", "--output=short-iso"],
            capture_output=True, text=True, timeout=5,
        )
        result["journal"] = proc.stdout.strip()
    except Exception as exc:
        result["journal"] = f"Error: {exc}"

    return result

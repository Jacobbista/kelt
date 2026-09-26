"""Admin endpoints (restart backend, etc.)."""

import logging
import subprocess
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_admin
from app.config import settings
from app.services.audit import write_audit

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
log = logging.getLogger(__name__)


@router.get("/watchdog-token")
def watchdog_token(_=Depends(require_admin)) -> dict[str, str]:
    """Return the watchdog shared secret to authenticated admin callers.

    The frontend caches the value in memory so it can authenticate against
    the local watchdog process even while the backend itself is down.
    """
    return {"token": settings.admin_token}


@router.post("/restart-backend")
def restart_backend(_=Depends(require_admin)) -> dict[str, Any]:
    """
    Restart the dashboard-backend systemd service (admin role).
    Requires sudo (vagrant user needs NOPASSWD for systemctl restart).
    This process will be killed by systemd; the response may not be delivered.
    When the backend itself is down, the watchdog (its own process, guarded by
    DASHBOARD_ADMIN_TOKEN) restarts it instead.
    """

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
def service_status(_=Depends(require_admin)) -> dict[str, Any]:
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

"""Dev frontend control.

The prod (cluster pod) dashboard is the always-on baseline. The Vite dev
frontend is an opt-in extra layered on top, running as a systemd unit on
the ansible VM. These endpoints let the prod UI inspect and toggle that
unit without exposing arbitrary sudo. Scope is enforced by the
/etc/sudoers.d/dashboard-dev-frontend-control rule installed by
ansible/phases/09-dashboard.
"""

import logging
import subprocess
from typing import Any

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.services.audit import write_audit

router = APIRouter(prefix="/api/v1/dev-frontend", tags=["dev-frontend"])
log = logging.getLogger(__name__)


def _is_active(svc: str) -> bool:
    try:
        proc = subprocess.run(
            ["sudo", "systemctl", "is-active", svc],
            capture_output=True, text=True, timeout=5,
        )
        return proc.stdout.strip() == "active"
    except Exception as exc:
        log.warning("is-active %s failed: %s", svc, exc)
        return False


def _status_payload() -> dict[str, Any]:
    svc = settings.frontend_service_name
    return {
        "service": svc,
        "is_active": _is_active(svc),
        "url": settings.dev_external_url or "",
    }


@router.get("/status")
def status() -> dict[str, Any]:
    return _status_payload()


@router.post("/enable")
def enable() -> dict[str, Any]:
    svc = settings.frontend_service_name
    write_audit("dev_frontend.enable", {"service": svc})
    try:
        subprocess.run(
            ["sudo", "systemctl", "start", svc],
            capture_output=True, text=True, timeout=10, check=True,
        )
    except subprocess.CalledProcessError as exc:
        log.error("systemctl start %s failed: %s", svc, exc.stderr)
        raise HTTPException(500, detail=exc.stderr or str(exc)) from exc
    except FileNotFoundError:
        raise HTTPException(500, "systemctl not found") from None
    return _status_payload()


@router.post("/disable")
def disable() -> dict[str, Any]:
    svc = settings.frontend_service_name
    write_audit("dev_frontend.disable", {"service": svc})
    try:
        subprocess.run(
            ["sudo", "systemctl", "stop", svc],
            capture_output=True, text=True, timeout=10, check=True,
        )
    except subprocess.CalledProcessError as exc:
        log.error("systemctl stop %s failed: %s", svc, exc.stderr)
        raise HTTPException(500, detail=exc.stderr or str(exc)) from exc
    except FileNotFoundError:
        raise HTTPException(500, "systemctl not found") from None
    return _status_payload()

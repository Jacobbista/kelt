"""Pieces and operations (admin only: included with the admin dependency)."""
import subprocess
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StrictInt

from app.auth import Principal, get_principal
from app.services import pieces_service as ps
from app.services.audit import write_audit
from app.services.k8s_service import K8sService, get_k8s_service
from app.services.ran_chain import attach_verdict, detach_verdict
from app.services.ran_service import RanService

router = APIRouter(prefix="/api/v1", tags=["pieces"])


def _checks(k8s: K8sService = Depends(get_k8s_service)) -> dict:
    ran = RanService(k8s)

    def ran_link_up() -> tuple[bool, str]:
        iface = ran._ran_iface()
        state = ran._nic_state(iface or "")
        return state == "up", f"{iface or 'the RAN interface'} is {state.replace('_', ' ')}"

    def ran_attached() -> tuple[bool, str]:
        return attach_verdict(ran.attachment_facts())

    def ran_detached() -> tuple[bool, str]:
        return detach_verdict(ran.attachment_facts())

    return {"ran_link_up": ran_link_up, "ran_attached": ran_attached, "ran_detached": ran_detached}


def _runner_error(exc: subprocess.CalledProcessError) -> HTTPException:
    last = (exc.stderr or exc.stdout or "").strip().splitlines()[-1:] or ["runner failed"]
    return HTTPException(502, detail=last[0])


@router.get("/pieces")
def pieces() -> dict[str, Any]:
    return ps.list_pieces()


class RunRequest(BaseModel):
    confirm: str | None = None  # the typed word, for a piece with a confirm_word


@router.post("/pieces/{name}/run")
def run_piece(name: str, req: RunRequest | None = None, who: Principal = Depends(get_principal)) -> dict[str, Any]:
    try:
        return ps.start(name, user=who.username, confirm=(req.confirm if req else None))
    except KeyError:
        raise HTTPException(404, detail=f"unknown piece {name!r}")
    except subprocess.CalledProcessError as exc:
        raise _runner_error(exc)


@router.get("/operations")
def operations(state: str | None = None) -> list[dict[str, Any]]:
    try:
        return ps.list_operations(state)
    except subprocess.CalledProcessError as exc:
        raise _runner_error(exc)


class RetentionRequest(BaseModel):
    max_age_days: StrictInt
    max_mb: StrictInt


# Declared before /operations/{op_id}, which would otherwise match "retention".
@router.get("/operations/retention")
def get_retention() -> dict[str, Any]:
    try:
        return ps.retention()
    except subprocess.CalledProcessError as exc:
        raise _runner_error(exc)


@router.put("/operations/retention")
def put_retention(req: RetentionRequest, who: Principal = Depends(get_principal)) -> dict[str, Any]:
    try:
        out = ps.set_retention(req.max_age_days, req.max_mb)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    except subprocess.CalledProcessError as exc:
        raise _runner_error(exc)
    write_audit("operations.retention", {"user": who.username, "max_age_days": out["max_age_days"],
                                         "max_mb": out["max_mb"]})
    return out


@router.get("/operations/{op_id}")
def operation(op_id: str, checks: dict = Depends(_checks)) -> dict[str, Any]:
    try:
        return ps.get_operation(op_id, checks=checks)
    except KeyError:
        raise HTTPException(404, detail="no such operation")
    except subprocess.CalledProcessError as exc:
        raise _runner_error(exc)

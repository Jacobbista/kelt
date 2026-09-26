"""Isolation: the plane filter and the NetworkPolicies, read-only.

See app/services/isolation_service.py and docs/dashboard/modules.md.
"""
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.isolation_service import IsolationService

router = APIRouter(prefix="/api/v1/isolation", tags=["isolation"])


class FlowCheck(BaseModel):
    source: str
    destination: dict[str, Any]


def _svc() -> IsolationService:
    return IsolationService()


@router.get("/planes")
async def planes(window: str = Query("24h")) -> dict[str, Any]:
    try:
        return await _svc().planes(window)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/planes/samples")
def samples(limit: int = Query(5, ge=1, le=50)) -> dict[str, Any]:
    return _svc().samples(limit)


@router.get("/policies")
def policies() -> dict[str, Any]:
    return _svc().policies()


@router.get("/targets")
def targets() -> dict[str, Any]:
    return _svc().targets()


# A POST that only evaluates: nothing is sent and nothing changes, so viewers may
# run it, like the other diagnostics (docs/security/iam.md).
@router.post("/check")
def check(body: FlowCheck) -> dict[str, Any]:
    try:
        return _svc().check(body.source, body.destination)
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc

"""Dashboard self-update API: report deployed-vs-registry status for the
dashboard's own components (frontend, docs) and trigger a targeted rollout.

read_router  -> GET status              (viewer)
write_router -> POST .../update          (admin)
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.services.audit import write_audit
from app.services.k8s_service import K8sService, get_k8s_service
from app.services.selfupdate_service import SelfUpdateService

read_router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard-selfupdate"])
write_router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard-selfupdate"])


def _svc(k8s: K8sService = Depends(get_k8s_service)) -> SelfUpdateService:
    return SelfUpdateService(k8s)


@read_router.get("/components")
def components(svc: SelfUpdateService = Depends(_svc)) -> list[dict[str, Any]]:
    return svc.status()


@write_router.post("/components/{name}/update")
def update_component(name: str, svc: SelfUpdateService = Depends(_svc)) -> dict[str, Any]:
    try:
        result = svc.update(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    write_audit("dashboard.component.update", {"name": name})
    return result

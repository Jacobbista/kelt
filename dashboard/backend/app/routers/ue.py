import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.services.k8s_service import K8sService, get_k8s_service
from app.services.prometheus_service import PrometheusService, get_prometheus_service
from app.services.ue_service import UEService

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/ue", tags=["ue-monitoring"])


def _get_ue(
    k8s: K8sService = Depends(get_k8s_service),
    prom: PrometheusService = Depends(get_prometheus_service),
) -> UEService:
    return UEService(k8s, prom)


@router.get("/summary")
async def ue_summary(svc: UEService = Depends(_get_ue)) -> dict[str, Any]:
    try:
        return await svc.get_summary()
    except Exception as exc:
        log.exception("UE summary failed")
        raise HTTPException(500, detail=str(exc)) from exc


@router.get("/events")
def ue_events(
    minutes: int = Query(10, ge=1, le=60),
    svc: UEService = Depends(_get_ue),
) -> list[dict[str, Any]]:
    try:
        return svc.get_events(minutes=minutes)
    except Exception as exc:
        log.exception("UE events failed")
        raise HTTPException(500, detail=str(exc)) from exc


@router.get("/active")
def ue_active(svc: UEService = Depends(_get_ue)) -> list[dict[str, Any]]:
    try:
        return svc.get_active_ues()
    except Exception as exc:
        log.exception("Active UEs failed")
        raise HTTPException(500, detail=str(exc)) from exc


@router.get("/pods")
def ue_pods(svc: UEService = Depends(_get_ue)) -> list[dict[str, Any]]:
    try:
        return svc.get_ue_pods()
    except Exception as exc:
        log.exception("UE pods failed")
        raise HTTPException(500, detail=str(exc)) from exc


@router.get("/gnbs")
def ue_gnbs(svc: UEService = Depends(_get_ue)) -> list[dict[str, Any]]:
    """Connected gNBs from AMF infoAPI (gnb_id, plmn, peer, num_connected_ues)."""
    try:
        return svc.get_gnb_info()
    except Exception as exc:
        log.exception("gNB info failed")
        raise HTTPException(500, detail=str(exc)) from exc


class PingRequest(BaseModel):
    pod: str
    target: str = "8.8.8.8"
    count: int = 4


class IperfRequest(BaseModel):
    pod: str
    server: str = "10.45.0.1"
    duration: int = 5


@router.post("/test/ping")
def ue_test_ping(req: PingRequest, svc: UEService = Depends(_get_ue)) -> dict[str, Any]:
    try:
        return svc.run_ping(req.pod, target=req.target, count=req.count)
    except Exception as exc:
        log.exception("Ping test failed")
        raise HTTPException(500, detail=str(exc)) from exc


@router.post("/test/iperf")
def ue_test_iperf(req: IperfRequest, svc: UEService = Depends(_get_ue)) -> dict[str, Any]:
    try:
        return svc.run_iperf(req.pod, server=req.server, duration=req.duration)
    except Exception as exc:
        log.exception("iperf test failed")
        raise HTTPException(500, detail=str(exc)) from exc

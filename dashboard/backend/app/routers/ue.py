import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_admin
from pydantic import BaseModel, Field

from app.services.k8s_service import K8sService, get_k8s_service
from app.services.mongo_service import MongoService, get_mongo_service
from app.services.prometheus_service import PrometheusService, get_prometheus_service
from app.services.ue_service import UEService

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/ue", tags=["ue-monitoring"])


def _get_ue(
    k8s: K8sService = Depends(get_k8s_service),
    prom: PrometheusService = Depends(get_prometheus_service),
    mongo: MongoService = Depends(get_mongo_service),
) -> UEService:
    return UEService(k8s, prom, mongo=mongo)


@router.get("/summary")
async def ue_summary(
    window: int = Query(
        300, ge=60, le=86400,
        description="Counter window in seconds (increase() range). Gauges ignore this.",
    ),
    svc: UEService = Depends(_get_ue),
) -> dict[str, Any]:
    try:
        return await svc.get_summary(window_seconds=window)
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
async def ue_active(svc: UEService = Depends(_get_ue)) -> list[dict[str, Any]]:
    try:
        return await svc.get_active_ues()
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


# Image uploads are stored inline as data URLs in the Mongo personalization
# document. The frontend resizes + recompresses to WebP client-side so even a
# 10 MB camera photo lands here as a ~20 KB thumbnail. These caps are a hard
# safety net, not the normal payload size.
_IMAGE_ALLOWED_MIMES = ("image/webp", "image/png", "image/jpeg", "image/jpg")
_IMAGE_MAX_DATA_URL_LEN = 150 * 1024  # ~110 KB raw after base64 decoding


class UePersonalizationPayload(BaseModel):
    nickname: str | None = Field(default=None, max_length=64)
    icon: str | None = Field(default=None, max_length=64)
    image: str | None = Field(
        default=None,
        max_length=_IMAGE_MAX_DATA_URL_LEN,
        description="Data URL (data:image/webp;base64,...). Pass '' to clear.",
    )


def _validate_image_data_url(value: str) -> None:
    """Reject oversized or unsupported image uploads.

    Raises HTTPException on failure; returns silently on success. An empty
    string is treated as a "clear the image" request and passes through.
    """
    if value == "":
        return
    if len(value) > _IMAGE_MAX_DATA_URL_LEN:
        raise HTTPException(
            413,
            detail=(
                f"image data URL is {len(value)} bytes, max {_IMAGE_MAX_DATA_URL_LEN}. "
                "Resize client-side before upload."
            ),
        )
    if not value.startswith("data:"):
        raise HTTPException(400, detail="image must be a data URL (data:image/...;base64,...)")
    header, _, _ = value.partition(",")
    # header looks like "data:image/webp;base64"
    mime = header[5:].split(";", 1)[0].strip().lower()
    if mime not in _IMAGE_ALLOWED_MIMES:
        raise HTTPException(
            400,
            detail=f"image MIME '{mime}' not allowed. Use one of: {_IMAGE_ALLOWED_MIMES}",
        )
    if ";base64" not in header:
        raise HTTPException(400, detail="image must be base64-encoded (data:...;base64,...)")


@router.get("/personalizations")
def list_ue_personalizations(
    mongo: MongoService = Depends(get_mongo_service),
) -> list[dict[str, Any]]:
    """Return all dashboard-side UE customizations (nickname/icon/image by IMSI)."""
    try:
        return mongo.list_ue_personalizations()
    except Exception as exc:
        log.exception("list UE personalizations failed")
        raise HTTPException(500, detail=str(exc)) from exc


# Ping / iperf above are diagnostics and stay open to a viewer; these two persist
# operator-authored data, so they are admin-only.
@router.put("/personalizations/{imsi}", dependencies=[Depends(require_admin)])
def upsert_ue_personalization(
    imsi: str,
    payload: UePersonalizationPayload,
    mongo: MongoService = Depends(get_mongo_service),
) -> dict[str, Any]:
    if not imsi.isdigit() or not 14 <= len(imsi) <= 15:
        raise HTTPException(400, detail="imsi must be 14-15 digits")
    if payload.image is not None:
        _validate_image_data_url(payload.image)
    try:
        return mongo.upsert_ue_personalization(
            imsi=imsi,
            nickname=payload.nickname,
            icon=payload.icon,
            image=payload.image,
        )
    except Exception as exc:
        log.exception("upsert UE personalization failed for %s", imsi)
        raise HTTPException(500, detail=str(exc)) from exc


@router.delete("/personalizations/{imsi}", dependencies=[Depends(require_admin)])
def delete_ue_personalization(
    imsi: str,
    mongo: MongoService = Depends(get_mongo_service),
) -> dict[str, bool]:
    try:
        return {"deleted": mongo.delete_ue_personalization(imsi)}
    except Exception as exc:
        log.exception("delete UE personalization failed for %s", imsi)
        raise HTTPException(500, detail=str(exc)) from exc


@router.get("/logs/{nf}")
def nf_raw_logs(
    nf: str,
    tail: int = Query(100, ge=10, le=2000),
    svc: UEService = Depends(_get_ue),
) -> dict[str, Any]:
    """Return raw log lines for a 5G NF pod (amf, smf, udm, …).

    Useful for verifying that log-parsing regexes match the actual Open5GS output.
    """
    allowed = {"amf", "smf", "udm", "udr", "ausf", "nrf", "pcf", "bsf", "nssf", "upf-cloud", "upf-edge"}
    if nf not in allowed:
        raise HTTPException(400, detail=f"nf must be one of: {sorted(allowed)}")
    try:
        raw = svc._read_deploy_logs(nf, tail=tail)
        lines = raw.splitlines()
        return {"nf": nf, "tail": tail, "count": len(lines), "lines": lines}
    except Exception as exc:
        log.exception("Raw logs fetch failed for %s", nf)
        raise HTTPException(500, detail=str(exc)) from exc

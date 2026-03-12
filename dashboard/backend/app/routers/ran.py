import asyncio
import json
import logging
import queue
import threading
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services.k8s_service import K8sService, get_k8s_service
from app.services.ran_service import RanService
from app.services.ueransim_service import UeransimService

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/ran", tags=["ran"])

RAN_STATUS_TIMEOUT_S = 15


class GnbFormRequest(BaseModel):
    name: str | None = None  # auto-generated if omitted
    cell_id: int = Field(default=1, ge=1, le=99)
    nci: str = Field(default="0x000000010")
    tac: int = Field(default=1, ge=1, le=999)
    slices: list[dict[str, int]] = Field(default=[{"sst": 1, "sd": 1}])
    mcc: str | None = None
    mnc: str | None = None
    image: str | None = None
    resources: dict[str, Any] | None = None


class UeFormRequest(BaseModel):
    name: str | None = None  # auto-generated if omitted
    cell_id: int = Field(default=1, ge=1, le=99)
    gnb_name: str = Field(min_length=3, max_length=63)
    apn: str = Field(default="internet", min_length=1, max_length=64)
    sst: int = Field(default=1, ge=0, le=255)
    sd: int = Field(default=1, ge=0, le=16777215)
    imsi_start: str = Field(default="895", min_length=1, max_length=10)
    integrity_max_rate: str | None = None  # "full" or "64kbps"
    image: str | None = None
    resources: dict[str, Any] | None = None


class GnbPatchRequest(BaseModel):
    tac: int | None = None
    slices: list[dict[str, int]] | None = None
    mcc: str | None = None
    mnc: str | None = None


class UePatchRequest(BaseModel):
    apn: str | None = None
    sst: int | None = None
    sd: int | None = None
    imsi_start: str | None = None
    integrity_max_rate: str | None = None


class UeransimPatchRequest(BaseModel):
    replicas: int | None = Field(default=None, ge=0, le=128)
    nodeSelector: dict[str, str] | None = None
    affinity: dict[str, Any] | None = None
    tolerations: list[dict[str, Any]] | None = None
    resources: dict[str, Any] | None = None


def _get_ran(k8s: K8sService = Depends(get_k8s_service)) -> RanService:
    return RanService(k8s)


def _get_ueransim(k8s: K8sService = Depends(get_k8s_service)) -> UeransimService:
    return UeransimService(k8s)


# ── Physical RAN ─────────────────────────────────────────────────

@router.get("/status")
async def ran_status(ran: RanService = Depends(_get_ran)) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(asyncio.to_thread(ran.get_status), timeout=RAN_STATUS_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise HTTPException(504, "RAN status check timed out") from None
    except Exception as exc:
        log.exception("Failed to get RAN status")
        raise HTTPException(500, detail=str(exc)) from exc


@router.post("/enable")
def ran_enable(ran: RanService = Depends(_get_ran)) -> dict[str, Any]:
    try:
        return ran.enable()
    except Exception as exc:
        log.exception("Failed to enable physical RAN")
        raise HTTPException(500, detail=str(exc)) from exc


@router.post("/disable")
def ran_disable(ran: RanService = Depends(_get_ran)) -> dict[str, Any]:
    try:
        return ran.disable()
    except Exception as exc:
        log.exception("Failed to disable physical RAN")
        raise HTTPException(500, detail=str(exc)) from exc


# ── Combined mode status ─────────────────────────────────────────

@router.get("/modes/status")
def ran_modes_status(
    ran: RanService = Depends(_get_ran),
    ueransim: UeransimService = Depends(_get_ueransim),
) -> dict[str, Any]:
    try:
        physical = ran.get_status()
        sim = ueransim.status()
        warnings = []
        if physical.get("enabled") and sim.get("enabled"):
            warnings.append("coexistence_active")
        return {"physical": physical, "ueransim": sim, "warnings": warnings}
    except Exception as exc:
        log.exception("Failed to fetch RAN modes status")
        raise HTTPException(500, detail=str(exc)) from exc


@router.post("/modes/physical/enable")
def enable_physical_mode(ran: RanService = Depends(_get_ran)) -> dict[str, Any]:
    try:
        return ran.enable()
    except Exception as exc:
        log.exception("Failed to enable physical RAN mode")
        raise HTTPException(500, detail=str(exc)) from exc


@router.post("/modes/physical/enable/stream")
def enable_physical_mode_stream(ran: RanService = Depends(_get_ran)):
    """Stream progress events as NDJSON, then final result."""
    q: queue.Queue[dict[str, Any] | None] = queue.Queue()
    sentinel = None

    def on_progress(step: str, status: str, msg: str) -> None:
        q.put({"step": step, "status": status, "message": msg})

    def run() -> None:
        try:
            result = ran.enable(on_progress=on_progress)
            q.put({"result": result})
        except Exception as exc:
            log.exception("Failed to enable physical RAN mode")
            q.put({"error": str(exc)})
        finally:
            q.put(sentinel)

    def gen() -> Any:
        t = threading.Thread(target=run)
        t.start()
        while True:
            item = q.get()
            if item is sentinel:
                break
            yield json.dumps(item) + "\n"

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/modes/physical/disable")
def disable_physical_mode(ran: RanService = Depends(_get_ran)) -> dict[str, Any]:
    try:
        return ran.disable()
    except Exception as exc:
        log.exception("Failed to disable physical RAN mode")
        raise HTTPException(500, detail=str(exc)) from exc


@router.post("/modes/physical/disable/stream")
def disable_physical_mode_stream(ran: RanService = Depends(_get_ran)):
    """Stream progress events as NDJSON, then final result."""
    q: queue.Queue[dict[str, Any] | None] = queue.Queue()
    sentinel = None

    def on_progress(step: str, status: str, msg: str) -> None:
        q.put({"step": step, "status": status, "message": msg})

    def run() -> None:
        try:
            result = ran.disable(on_progress=on_progress)
            q.put({"result": result})
        except Exception as exc:
            log.exception("Failed to disable physical RAN mode")
            q.put({"error": str(exc)})
        finally:
            q.put(sentinel)

    def gen() -> Any:
        t = threading.Thread(target=run)
        t.start()
        while True:
            item = q.get()
            if item is sentinel:
                break
            yield json.dumps(item) + "\n"

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/modes/ueransim/enable")
def enable_ueransim_mode(ueransim: UeransimService = Depends(_get_ueransim)) -> dict[str, Any]:
    try:
        return ueransim.enable()
    except Exception as exc:
        log.exception("Failed to enable UERANSIM mode")
        raise HTTPException(500, detail=str(exc)) from exc


@router.post("/modes/ueransim/disable")
def disable_ueransim_mode(ueransim: UeransimService = Depends(_get_ueransim)) -> dict[str, Any]:
    try:
        return ueransim.disable()
    except Exception as exc:
        log.exception("Failed to disable UERANSIM mode")
        raise HTTPException(500, detail=str(exc)) from exc


# ── UERANSIM status + smart defaults ────────────────────────────

@router.get("/ueransim/status")
def ueransim_status(ueransim: UeransimService = Depends(_get_ueransim)) -> dict[str, Any]:
    try:
        return ueransim.status()
    except Exception as exc:
        log.exception("Failed to fetch UERANSIM status")
        raise HTTPException(500, detail=str(exc)) from exc


@router.get("/ueransim/defaults")
def ueransim_defaults(ueransim: UeransimService = Depends(_get_ueransim)) -> dict[str, Any]:
    """Smart defaults for creation forms: existing resources, cluster nodes, next names."""
    try:
        return ueransim.get_defaults()
    except Exception as exc:
        log.exception("Failed to fetch UERANSIM defaults")
        raise HTTPException(500, detail=str(exc)) from exc


# ── UERANSIM CRUD ────────────────────────────────────────────────

@router.post("/ueransim/gnbs/form")
def create_gnb_form(req: GnbFormRequest, ueransim: UeransimService = Depends(_get_ueransim)) -> dict[str, Any]:
    try:
        return ueransim.create_gnb_form(req.model_dump(exclude_none=True))
    except Exception as exc:
        log.exception("Failed to create gNB from form")
        raise HTTPException(400, detail=str(exc)) from exc


@router.post("/ueransim/ues/form")
def create_ue_form(req: UeFormRequest, ueransim: UeransimService = Depends(_get_ueransim)) -> dict[str, Any]:
    try:
        return ueransim.create_ue_form(req.model_dump(exclude_none=True))
    except Exception as exc:
        log.exception("Failed to create UE from form")
        raise HTTPException(400, detail=str(exc)) from exc


@router.post("/ueransim/gnbs")
def create_gnb(payload: dict[str, Any] = Body(default={}), ueransim: UeransimService = Depends(_get_ueransim)) -> dict[str, Any]:
    try:
        return ueransim.create_gnb(payload)
    except Exception as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.post("/ueransim/ues")
def create_ue(payload: dict[str, Any] = Body(default={}), ueransim: UeransimService = Depends(_get_ueransim)) -> dict[str, Any]:
    try:
        return ueransim.create_ue(payload)
    except Exception as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.patch("/ueransim/gnbs/{name}")
def patch_gnb(name: str, payload: UeransimPatchRequest = Body(default=UeransimPatchRequest()), ueransim: UeransimService = Depends(_get_ueransim)) -> dict[str, Any]:
    try:
        return ueransim.patch_gnb(name, payload.model_dump(exclude_none=True))
    except Exception as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.patch("/ueransim/ues/{name}")
def patch_ue(name: str, payload: UeransimPatchRequest = Body(default=UeransimPatchRequest()), ueransim: UeransimService = Depends(_get_ueransim)) -> dict[str, Any]:
    try:
        return ueransim.patch_ue(name, payload.model_dump(exclude_none=True))
    except Exception as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.post("/ueransim/gnbs/{name}/activate")
def activate_gnb(name: str, ueransim: UeransimService = Depends(_get_ueransim)) -> dict[str, Any]:
    try:
        return ueransim.activate_gnb(name)
    except Exception as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.post("/ueransim/gnbs/{name}/deactivate")
def deactivate_gnb(name: str, ueransim: UeransimService = Depends(_get_ueransim)) -> dict[str, Any]:
    try:
        return ueransim.deactivate_gnb(name)
    except Exception as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.post("/ueransim/ues/{name}/activate")
def activate_ue(name: str, ueransim: UeransimService = Depends(_get_ueransim)) -> dict[str, Any]:
    try:
        return ueransim.activate_ue(name)
    except Exception as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.post("/ueransim/ues/{name}/deactivate")
def deactivate_ue(name: str, ueransim: UeransimService = Depends(_get_ueransim)) -> dict[str, Any]:
    try:
        return ueransim.deactivate_ue(name)
    except Exception as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.delete("/ueransim/gnbs/{name}")
def delete_gnb(name: str, ueransim: UeransimService = Depends(_get_ueransim)) -> dict[str, Any]:
    try:
        return ueransim.delete_gnb(name)
    except Exception as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.delete("/ueransim/ues/{name}")
def delete_ue(name: str, ueransim: UeransimService = Depends(_get_ueransim)) -> dict[str, Any]:
    try:
        return ueransim.delete_ue(name)
    except Exception as exc:
        raise HTTPException(400, detail=str(exc)) from exc

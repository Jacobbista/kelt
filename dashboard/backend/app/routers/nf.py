import json
import logging
import queue
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services.k8s_service import K8sService, get_k8s_service
from app.services.nf_service import NFService

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/nf", tags=["nf-versions"])


def _get_nf(k8s: K8sService = Depends(get_k8s_service)) -> NFService:
    return NFService(k8s)


@router.get("/versions")
def nf_versions(svc: NFService = Depends(_get_nf)) -> list[dict[str, Any]]:
    """Per-NF comparison: deployed image vs available in 5g-nf-platform."""
    try:
        return svc.compare_versions()
    except Exception as exc:
        log.exception("nf_versions failed")
        raise HTTPException(500, detail=str(exc)) from exc


class NfUpdateRequest(BaseModel):
    nf:  str = Field(..., description="NF name, e.g. 'smf'")
    tag: str = Field(..., description="Image tag, e.g. '2.7.5-p2'")


@router.post("/update/stream")
def nf_update_stream(req: NfUpdateRequest, svc: NFService = Depends(_get_nf)) -> StreamingResponse:
    """Trigger ansible phase 05 for a single NF image update. Streams NDJSON progress."""
    allowed = {"amf", "smf", "upf-cloud", "upf-edge", "udm", "udr",
               "nrf", "pcf", "bsf", "nssf", "ausf"}
    if req.nf not in allowed:
        raise HTTPException(400, detail=f"nf must be one of: {sorted(allowed)}")
    if not req.tag or "/" in req.tag or ".." in req.tag:
        raise HTTPException(400, detail="invalid tag")

    q: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def run() -> None:
        try:
            def on_progress(line: str) -> None:
                q.put({"type": "log", "line": line})

            svc.update_nf(req.nf, req.tag, on_progress=on_progress)
            q.put({"type": "result", "status": "ok",
                   "detail": f"{req.nf} updated to {req.tag}"})
        except Exception as exc:
            log.exception("nf_update_stream failed for %s %s", req.nf, req.tag)
            q.put({"type": "error", "detail": str(exc)})
        finally:
            q.put(None)

    def gen():
        t = threading.Thread(target=run, daemon=True)
        t.start()
        while True:
            item = q.get()
            if item is None:
                break
            yield json.dumps(item) + "\n"

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

"""Edge apps platform (phase 12) console API.

Two routers share the /api/v1/apps prefix:
  - read_router  : GET inventory          -> included under _viewer
  - write_router : deploy / delete an app -> included under _admin

Mirrors the Northbound split (GET = viewer+admin, writes = admin only), enforced
at router-include time in app/main.py. Writes additionally require the
allow_workload_create policy gate (phase 09), exactly like the Northbound
deploy-from-image endpoint. See docs/security/iam.md and docs/architecture/edge-apps.md.
"""

import json
import logging
import queue
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.config import settings
from app.models import AppDeployRequest
from app.services.apps_service import AppsService
from app.services.audit import write_audit
from app.services.k8s_service import K8sService, get_k8s_service

log = logging.getLogger(__name__)

read_router = APIRouter(prefix="/api/v1/apps", tags=["apps"])
write_router = APIRouter(prefix="/api/v1/apps", tags=["apps"])


def _get_apps(k8s: K8sService = Depends(get_k8s_service)) -> AppsService:
    return AppsService(k8s)


@read_router.get("")
def list_apps(svc: AppsService = Depends(_get_apps)) -> dict[str, Any]:
    return svc.inventory()


@write_router.post("")
def deploy_app(req: AppDeployRequest, svc: AppsService = Depends(_get_apps)) -> dict[str, Any]:
    if not settings.allow_workload_create:
        raise HTTPException(status_code=403, detail="App deployment is disabled by policy")
    try:
        result = svc.deploy_app(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    write_audit("apps.deploy", {
        "name": req.name,
        "image": req.image,
        "port": req.port,
        "replicas": req.replicas,
        "expose": req.expose,
        "pull_secret": bool(req.image_pull_secret),
    })
    return result


@write_router.get("/registry-credentials")
def registry_credentials(svc: AppsService = Depends(_get_apps)) -> dict[str, Any]:
    # Admin-only (write_router): the registry basic-auth so an admin can docker push.
    write_audit("apps.registry_credentials.view", {})
    return svc.registry_credentials()


@write_router.post("/provision")
def provision(svc: AppsService = Depends(_get_apps)):
    # Deploy the platform from the UI (phase 12 + 11), streaming progress as NDJSON.
    if not settings.allow_workload_create:
        raise HTTPException(status_code=403, detail="App provisioning is disabled by policy")
    write_audit("apps.provision", {})
    q: queue.Queue[dict[str, Any] | None] = queue.Queue()
    sentinel = None

    def on_progress(line: str) -> None:
        q.put({"line": line})

    def run() -> None:
        try:
            svc.provision(on_progress=on_progress)
            q.put({"result": {"status": "provisioned"}})
        except Exception as exc:
            log.exception("Apps provision failed")
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


@write_router.delete("/{name}")
def delete_app(name: str, svc: AppsService = Depends(_get_apps)) -> dict[str, Any]:
    if not settings.allow_workload_create:
        raise HTTPException(status_code=403, detail="App management is disabled by policy")
    try:
        result = svc.delete_app(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    write_audit("apps.delete", {"name": name})
    return result

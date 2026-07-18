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
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from app.config import settings
from app.models import AppDeployRequest
from app.services.apps_service import AppDeployError, AppsService
from app.services.audit import write_audit
from app.services.k8s_service import K8sService, get_k8s_service


class GnbConsoleRequest(BaseModel):
    host: str
    port: int = 8400

log = logging.getLogger(__name__)

read_router = APIRouter(prefix="/api/v1/apps", tags=["apps"])
write_router = APIRouter(prefix="/api/v1/apps", tags=["apps"])
# Unauthenticated: only exposed app names + public URLs, for the pre-auth
# front-door welcome page (same data the static service catalogue already shows).
public_router = APIRouter(prefix="/api/v1/apps", tags=["apps"])


def _get_apps(k8s: K8sService = Depends(get_k8s_service)) -> AppsService:
    return AppsService(k8s)


@public_router.get("/public")
def public_apps(svc: AppsService = Depends(_get_apps)) -> dict[str, Any]:
    return svc.public_apps()


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


@write_router.get("/starter-kit")
def starter_kit(svc: AppsService = Depends(_get_apps)) -> Response:
    # Admin-only: zip of README + .env.example + deploy.sh, prefilled with the
    # registry host, handed to an app developer to build and push their image.
    return Response(
        content=svc.starter_kit_zip(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=kelt-edge-app.zip"},
    )


@write_router.get("/registry/images")
def registry_images(svc: AppsService = Depends(_get_apps)) -> dict[str, Any]:
    # Admin-only: lists repos/tags in the local registry (needs the basic-auth creds).
    return svc.registry_images()


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


# gNB management console: register/clear the physical gNB's web UI as an external
# endpoint reached at gnb.<base> via the dynamic apps route (no front-door change).
# Path is two segments so it never collides with the /{name} delete route below.
@read_router.get("/updates")
def app_updates(svc: AppsService = Depends(_get_apps)) -> dict[str, Any]:
    # Per app: is a newer image present in the registry for its tag (digest differs
    # from the running pod)? Best-effort; drives the "update available" suggestion.
    return svc.check_updates()


class SetImageRequest(BaseModel):
    image: str


@write_router.put("/{name}/image")
def set_app_image(name: str, req: SetImageRequest, svc: AppsService = Depends(_get_apps)) -> dict[str, Any]:
    # Retarget the app to a chosen registry tag (date-ordered picker in the UI),
    # keeping its other settings; rollout pulls it (imagePullPolicy: Always).
    if not settings.allow_workload_create:
        raise HTTPException(status_code=403, detail="App management is disabled by policy")
    try:
        result = svc.set_app_image(name, req.image)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    write_audit("apps.set_image", {"name": name, "image": req.image})
    return result


@read_router.get("/gnb/console")
def gnb_console(svc: AppsService = Depends(_get_apps)) -> dict[str, Any]:
    return svc.gnb_console_status()


@write_router.put("/gnb/console")
def set_gnb_console(req: GnbConsoleRequest, svc: AppsService = Depends(_get_apps)) -> dict[str, Any]:
    if not settings.allow_workload_create:
        raise HTTPException(status_code=403, detail="gNB console management is disabled by policy")
    try:
        result = svc.set_gnb_console(req.host, req.port)
    except AppDeployError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    write_audit("apps.gnb_console.set", {"origin": f"{req.host}:{req.port}"})
    return result


@write_router.delete("/gnb/console")
def clear_gnb_console(svc: AppsService = Depends(_get_apps)) -> dict[str, Any]:
    result = svc.clear_gnb_console()
    write_audit("apps.gnb_console.clear", {})
    return result


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

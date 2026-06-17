"""Northbound (positioning/CAMARA) service-management console API.

Two routers share the /api/v1/northbound prefix:
  - read_router  : GET inventory/adapters/contract     -> included under _viewer
  - write_router : adapter registry, deploy-from-image,
                   fusion config, managed image rollout -> included under _admin

The split mirrors the role model in docs/security/iam.md (GET = viewer+admin,
writes = admin only) at router-include time in app/main.py.
"""

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from app.config import settings
from app.models import (
    AdapterUpgradeRequest,
    AssetStoreRequest,
    CoreImageRequest,
    DeployImageRequest,
    FusionConfigPayload,
    ServiceConfigRequest,
    ServiceFileRequest,
    WorkloadDeployRequest,
)
from app.services.audit import write_audit
from app.services.k8s_service import K8sService, get_k8s_service
from app.services.northbound_service import GatewayError, NorthboundService

read_router = APIRouter(prefix="/api/v1/northbound", tags=["northbound"])
write_router = APIRouter(prefix="/api/v1/northbound", tags=["northbound"])


def _get_nb(k8s: K8sService = Depends(get_k8s_service)) -> NorthboundService:
    return NorthboundService(k8s)


# ── Reads (viewer or admin) ──────────────────────────────────────────────────
@read_router.get("/services")
def inventory(nb: NorthboundService = Depends(_get_nb)) -> dict[str, Any]:
    return nb.inventory()


@read_router.get("/adapters")
def list_adapters(nb: NorthboundService = Depends(_get_nb)) -> list[dict[str, Any]]:
    # Live registry from the engine: entries carry mixed-typed fields
    # (last_seen_s_ago float, fail_count int, in_cooldown bool), so the value type
    # is Any, not str.
    return nb.list_adapters()


@read_router.get("/contract")
def contract(nb: NorthboundService = Depends(_get_nb)) -> dict[str, Any]:
    return nb.contract()


@read_router.get("/readiness")
def service_readiness(nb: NorthboundService = Depends(_get_nb)) -> dict[str, Any]:
    # Per configurable service: needs_config + the list of missing required env /
    # unmounted *_FILE documents. Drives the "needs config" flag in the UI.
    return nb.service_readiness()


@read_router.get("/bindings")
def adapter_bindings(nb: NorthboundService = Depends(_get_nb)) -> dict[str, Any]:
    # Per consumer/field: current adapter binding + deployed candidates of the
    # matching kind. Drives the at-a-glance association, single-adapter auto-bind,
    # and the multi-adapter switcher. The bind itself reuses PUT /config.
    return nb.adapter_bindings()


@read_router.get("/contract/{service}")
def service_contract(service: str, nb: NorthboundService = Depends(_get_nb)) -> dict[str, Any]:
    # Live per-service contract (kind, external_origin var, required/recommended
    # env) fetched from the service's own /contract via the API-server proxy.
    # Drives the guided setup + reachability. Degrades to {available: False}.
    return nb.service_contract(service)


@read_router.get("/config/{service}")
def service_config(service: str, nb: NorthboundService = Depends(_get_nb)) -> dict[str, Any]:
    # Contract schema + current values (non-sensitive) / set-state (sensitive),
    # for the guided setup form. Sensitive values are never returned.
    return nb.service_config(service)


@read_router.get("/files/{service}")
def service_file(service: str, path: str, nb: NorthboundService = Depends(_get_nb)) -> dict[str, Any]:
    # Current content of a file-backed config field (a contract *_FILE path),
    # stored in the service's <name>-files ConfigMap. Drives the document editor.
    return nb.get_service_file(service, path)


# ── Writes (admin only) ──────────────────────────────────────────────────────
# Asset Identity Map (gateway = authority). Admin only; the caller's Bearer is
# forwarded to the gateway, which enforces camara-location-read (a dashboard-admin
# token is composite with it) and the org join. GETs live here too (not under the
# viewer router) because a viewer token lacks camara-location-read.
def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    return authorization.split(None, 1)[1].strip()


def _gateway_call(fn):
    try:
        return fn()
    except GatewayError as exc:
        raise HTTPException(status_code=exc.status, detail=f"gateway /assets: {exc.detail[:300]}")


@write_router.get("/assets")
def list_assets(
    authorization: str | None = Header(default=None),
    nb: NorthboundService = Depends(_get_nb),
) -> dict[str, Any]:
    token = _bearer_token(authorization)
    return _gateway_call(lambda: nb.list_assets(token))


@write_router.get("/assets/{asset_id}/details")
def asset_details(
    asset_id: str,
    authorization: str | None = Header(default=None),
    nb: NorthboundService = Depends(_get_nb),
) -> dict[str, Any]:
    token = _bearer_token(authorization)
    return _gateway_call(lambda: nb.asset_details(token, asset_id))


@write_router.put("/assets")
def put_assets(
    body: AssetStoreRequest,
    authorization: str | None = Header(default=None),
    nb: NorthboundService = Depends(_get_nb),
) -> dict[str, Any]:
    token = _bearer_token(authorization)
    result = _gateway_call(lambda: nb.put_assets(token, body.model_dump()))
    write_audit("northbound.assets.put", {"count": result.get("count", 0)})
    return result


# No manual adapter register: adapters self-register with the engine (v0.6.0).
# DELETE force-removes a stale registry entry (engine DELETE /adapters/{name}).
@write_router.delete("/adapters/{name}")
def unregister_adapter(name: str, nb: NorthboundService = Depends(_get_nb)) -> dict[str, Any]:
    result = nb.unregister_adapter(name)
    write_audit("northbound.adapter.unregister", {"name": name})
    return result


@write_router.post("/adapters/{name}/upgrade")
def upgrade_adapter(
    name: str,
    req: AdapterUpgradeRequest,
    nb: NorthboundService = Depends(_get_nb),
) -> dict[str, Any]:
    # In-place image upgrade of a catalog adapter (config preserved). Admin only.
    try:
        result = nb.upgrade_adapter(name, req.image)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    write_audit("northbound.adapter.upgrade", {"name": name, "image": req.image})
    return result


@write_router.post("/deploy")
def deploy_image(
    req: DeployImageRequest,
    nb: NorthboundService = Depends(_get_nb),
) -> dict[str, Any]:
    if not settings.allow_workload_create:
        raise HTTPException(status_code=403, detail="Workload creation is disabled by policy")
    try:
        result = nb.deploy_image(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    write_audit("northbound.deploy", {
        "name": req.name,
        "image": req.image,
        "port": req.port,
        "kind": req.kind,
        "pull_secret": bool(req.image_pull_secret),
    })
    return result


@write_router.post("/workloads")
def deploy_workload(
    req: WorkloadDeployRequest,
    nb: NorthboundService = Depends(_get_nb),
) -> dict[str, Any]:
    if not settings.allow_workload_create:
        raise HTTPException(status_code=403, detail="Workload creation is disabled by policy")
    try:
        result = nb.deploy_workload(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    write_audit("northbound.workload.deploy", {
        "name": req.name, "image": req.image, "port": req.port, "namespace": req.namespace,
    })
    return result


@write_router.delete("/workloads/{name}")
def delete_workload(name: str, nb: NorthboundService = Depends(_get_nb)) -> dict[str, Any]:
    if not settings.allow_workload_create:
        raise HTTPException(status_code=403, detail="Workload management is disabled by policy")
    try:
        result = nb.delete_adapter_workload(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    write_audit("northbound.workload.delete", {"name": name})
    return result


@write_router.put("/config/{service}")
def apply_service_config(
    service: str,
    req: ServiceConfigRequest,
    nb: NorthboundService = Depends(_get_nb),
) -> dict[str, Any]:
    # Guided-setup apply. Routes each var by the contract's sensitive flag
    # (Secret vs ConfigMap), then rolls the deployment. Sensitive values are
    # never logged in the audit (only the var names that changed).
    try:
        result = nb.apply_service_config(service, req.values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    write_audit("northbound.config.apply", {"service": service, "vars": sorted(req.values.keys())})
    return result


@write_router.put("/files/{service}")
def apply_service_file(
    service: str,
    req: ServiceFileRequest,
    nb: NorthboundService = Depends(_get_nb),
) -> dict[str, Any]:
    # Store a file-backed config document in the <name>-files ConfigMap and mount
    # it at the declared path, then roll. The content itself is not audited (may be
    # large / sensitive); only the service + path are.
    try:
        result = nb.apply_service_file(service, req.path, req.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    write_audit("northbound.file.apply", {"service": service, "path": req.path})
    return result


@write_router.put("/fusion")
def set_fusion(
    payload: FusionConfigPayload,
    nb: NorthboundService = Depends(_get_nb),
) -> dict[str, Any]:
    result = nb.set_fusion(payload)
    write_audit("northbound.fusion", payload.model_dump(exclude_none=True))
    return result


@write_router.post("/managed/{deployment}/image")
def set_managed_image(
    deployment: str,
    req: CoreImageRequest,
    nb: NorthboundService = Depends(_get_nb),
) -> dict[str, Any]:
    try:
        result = nb.set_managed_image(deployment, req.image)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    write_audit("northbound.managed.image", {"deployment": deployment, "image": req.image})
    return result

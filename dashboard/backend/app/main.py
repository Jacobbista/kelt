import json
import logging
import traceback

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import require_admin, require_viewer_or_admin
from app.config import settings
from app.routers.admin import router as admin_router
from app.routers.cluster import router as cluster_router
from app.routers.dev_frontend import router as dev_frontend_router
from app.routers.experiments import router as experiments_router
from app.routers.health import router as health_router
from app.routers.kubernetes import router as kubernetes_router
from app.routers.logs_ws import router as logs_ws_router
from app.routers.metrics import router as metrics_router
from app.routers.network import router as network_router
from app.routers.pods import router as pods_router
from app.routers.ran import router as ran_router
from app.routers.sniffer import router as sniffer_router
from app.routers.subscribers import router as subscribers_router
from app.routers.topology import router as topology_router
from app.routers.traffic import router as traffic_router
from app.routers.time_sync import router as time_sync_router
from app.routers.exec_ws import router as exec_ws_router
from app.routers.ue import router as ue_router
from app.routers.nf import router as nf_router
from app.routers.northbound import read_router as northbound_read_router
from app.routers.northbound import write_router as northbound_write_router
from app.routers.apps import read_router as apps_read_router
from app.routers.apps import write_router as apps_write_router
from app.routers.branding import read_router as branding_read_router
from app.routers.branding import write_router as branding_write_router
from app.routers.selfupdate import read_router as selfupdate_read_router
from app.routers.selfupdate import write_router as selfupdate_write_router

log = logging.getLogger(__name__)

# Serve the auto-generated OpenAPI + Swagger UI under /api so it rides the
# existing single-origin /api reverse proxy (the frontend nginx proxies /api).
app = FastAPI(
    title=settings.app_name,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)


class CatchAllMiddleware(BaseHTTPMiddleware):
    """Catch unhandled exceptions and return a proper JSON 500 *before* CORS runs.

    Starlette's CORSMiddleware sits above this in the stack, so the 500
    response returned here still gets CORS headers applied normally.
    """

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            log.exception("Unhandled exception on %s %s", request.method, request.url.path)
            body = json.dumps({"detail": str(exc)})
            return Response(content=body, status_code=500, media_type="application/json")


# Single-origin via Vite proxy means browser CORS preflights never reach the
# backend in normal operation. The allow-list still matters for direct
# NodePort access (LAN debugging, curl). Comma-separated origins via
# DASHBOARD_CORS_ORIGIN, or "*" to keep the wildcard.
_cors_origins = (
    ["*"] if settings.cors_origin.strip() == "*"
    else [o.strip() for o in settings.cors_origin.split(",") if o.strip()]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(CatchAllMiddleware)

# Auth dependency groups. The full role-to-endpoint matrix is documented in
# docs/security/iam.md. While settings.skip_auth is True (default until phase
# 08 IAM is wired into production), require_* deps return a synthetic admin
# so the dashboard remains operable.
_viewer = [Depends(require_viewer_or_admin)]
_admin = [Depends(require_admin)]

# Unauthenticated: liveness probes (browser useBackendHealth, watchdog) and
# the legacy admin lane gated by DASHBOARD_ADMIN_TOKEN.
app.include_router(health_router)
app.include_router(admin_router)

# Dev frontend control: lets the prod UI toggle the opt-in Vite dev
# frontend. Admin-gated; the scoped sudoers rule is installed by phase
# 09 and only covers start/stop/is-active on the dashboard-frontend unit.
app.include_router(dev_frontend_router, dependencies=_admin)

# Viewer-or-admin: cluster/NF status, pod metadata, log streaming, metrics.
app.include_router(cluster_router,     dependencies=_viewer)
app.include_router(kubernetes_router,  dependencies=_viewer)
app.include_router(pods_router,        dependencies=_viewer)
app.include_router(logs_ws_router,     dependencies=_viewer)
app.include_router(topology_router,    dependencies=_viewer)
app.include_router(network_router,     dependencies=_viewer)
app.include_router(metrics_router,     dependencies=_viewer)
app.include_router(traffic_router,     dependencies=_viewer)
app.include_router(ue_router,          dependencies=_viewer)
app.include_router(time_sync_router,   dependencies=_viewer)
app.include_router(experiments_router, dependencies=_viewer)
# Northbound console reads (inventory, adapter registry, contract guidance).
app.include_router(northbound_read_router, dependencies=_viewer)
app.include_router(apps_read_router, dependencies=_viewer)
app.include_router(branding_read_router, dependencies=_viewer)
# Dashboard self-update status (deployed-vs-registry for frontend/docs).
app.include_router(selfupdate_read_router, dependencies=_viewer)

# Admin-only: privileged actions with cluster-wide blast radius.
# - subscribers: read access also gated because the records contain K and OPc
#                (5G AKA crypto roots). Viewer must not see them.
# - nf:          update/stream triggers an ansible-playbook subprocess.
# - ran:         physical-mode enable/disable runs node-level network reconfig.
# - sniffer:     packet capture inside privileged UPF pod.
# - exec_ws:     pod shell.
app.include_router(subscribers_router, dependencies=_admin)
app.include_router(nf_router,          dependencies=_admin)
app.include_router(ran_router,         dependencies=_admin)
app.include_router(sniffer_router,     dependencies=_admin)
app.include_router(exec_ws_router,     dependencies=_admin)
# Northbound console writes: adapter registry, deploy-from-image (also gated by
# settings.allow_workload_create), fusion config, managed image rollout.
app.include_router(northbound_write_router, dependencies=_admin)
app.include_router(apps_write_router, dependencies=_admin)
app.include_router(branding_write_router, dependencies=_admin)
# Dashboard self-update action: targeted rollout of a component (admin).
app.include_router(selfupdate_write_router, dependencies=_admin)


@app.on_event("startup")
def _sync_subscriber_snapshot_on_startup() -> None:
    """Align the subscribers-snapshot ConfigMap with current MongoDB state.

    Runs best-effort: any failure (Mongo down, k8s API unreachable) is logged
    and the app continues. This catches the case where the playbook seeded
    MongoDB but the dashboard backend was restarted afterwards, as well as
    manual MongoDB edits performed outside the dashboard API.

    See docs/architecture/subscriber-persistence.md
    """
    try:
        from app.services.mongo_service import MongoService
        mongo = MongoService()
        if not mongo.ping():
            log.info("subscriber snapshot startup sync: MongoDB not reachable yet, skipping")
            return
        subs = mongo.list_subscribers()
        if not subs and mongo.snapshot.exists():
            log.info("subscriber snapshot startup sync: MongoDB empty, leaving existing snapshot untouched")
            return
        ok = mongo.sync_snapshot()
        log.info("subscriber snapshot startup sync: ok=%s count=%d", ok, len(subs))
    except Exception:
        log.exception("subscriber snapshot startup sync failed")

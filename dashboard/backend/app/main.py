import json
import logging
import traceback

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers.admin import router as admin_router
from app.routers.cluster import router as cluster_router
from app.routers.experiments import router as experiments_router
from app.routers.health import router as health_router
from app.routers.logs_ws import router as logs_ws_router
from app.routers.metrics import router as metrics_router
from app.routers.network import router as network_router
from app.routers.pods import router as pods_router
from app.routers.ran import router as ran_router
from app.routers.sniffer import router as sniffer_router
from app.routers.subscribers import router as subscribers_router
from app.routers.topology import router as topology_router
from app.routers.traffic import router as traffic_router
from app.routers.ue import router as ue_router

log = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name)


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


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(CatchAllMiddleware)

app.include_router(health_router)
app.include_router(admin_router)
app.include_router(cluster_router)
app.include_router(pods_router)
app.include_router(logs_ws_router)
app.include_router(topology_router)
app.include_router(network_router)
app.include_router(subscribers_router)
app.include_router(metrics_router)
app.include_router(ran_router)
app.include_router(sniffer_router)
app.include_router(traffic_router)
app.include_router(ue_router)
app.include_router(experiments_router)

"""Overall state for the header's status pill. See app/services/status_service.py."""
from typing import Any, Callable

from fastapi import APIRouter

from app.routers.cluster import _deduplicate_pods
from app.services.amf_cni_service import check_alert
from app.services.k8s_service import K8sService
from app.services.network_health_service import NetworkHealthService
from app.services.network_plan import plan_value
from app.services.status_service import StatusService

router = APIRouter(prefix="/api/v1/status", tags=["status"])

# Network check results older than this are not reported as the current state.
CHECKS_MAX_AGE = 600


def _checks(k8s: K8sService) -> list[dict[str, Any]]:
    svc = NetworkHealthService(k8s)
    age = svc.last_age()
    return [{**c, "age_s": age} for c in svc.get_cached(CHECKS_MAX_AGE)]


def summarize(make_k8s: Callable[[], K8sService]) -> dict[str, Any]:
    # Without the Kubernetes API nothing else can be read: that is the answer,
    # and the worst one (not a 503 the pill would show as "unavailable").
    try:
        k8s = make_k8s()
    except Exception as exc:
        return {"state": "error", "problems": [
            {"area": "Cluster", "text": f"Kubernetes API unreachable: {exc}", "severity": "error"}]}
    return StatusService(
        nodes=k8s.list_nodes,
        pods=lambda: _deduplicate_pods(k8s.list_pods(plan_value("namespace_5g"))),
        checks=lambda: _checks(k8s),
        amf_alert=lambda: check_alert(k8s, strict=True),
    ).summary()


@router.get("/summary")
def summary() -> dict[str, Any]:
    return summarize(K8sService)

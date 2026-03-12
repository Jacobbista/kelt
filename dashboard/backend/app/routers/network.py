import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.services.k8s_service import K8sService, get_k8s_service
from app.services.network_health_service import NetworkHealthService

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/network", tags=["network"])

INTERFACE_LABELS = {
    "n1-net": "N1",
    "n2-net": "N2",
    "n3-net": "N3",
    "n4-net": "N4",
    "n6c-net": "N6-Cloud",
    "n6e-net": "N6-Edge",
    "n2-physical": "N2-Physical",
}


def _get_health_svc(
    k8s: K8sService = Depends(get_k8s_service),
) -> NetworkHealthService:
    return NetworkHealthService(k8s)


@router.get("/health")
def network_health(
    svc: NetworkHealthService = Depends(_get_health_svc),
) -> list[dict[str, Any]]:
    """Return cached health check results (runs on first call)."""
    cached = svc.get_cached()
    if cached:
        return cached
    try:
        return svc.run_health_checks()
    except Exception as exc:
        log.exception("Network health check failed")
        raise HTTPException(500, detail=str(exc)) from exc


@router.post("/health/run")
def run_network_health(
    svc: NetworkHealthService = Depends(_get_health_svc),
) -> list[dict[str, Any]]:
    """Trigger an immediate connectivity test across all N-interfaces."""
    try:
        return svc.run_health_checks()
    except Exception as exc:
        log.exception("Network health run failed")
        raise HTTPException(500, detail=str(exc)) from exc


@router.get("/n6-nat")
def n6_nat_diagnostics(
    svc: NetworkHealthService = Depends(_get_health_svc),
) -> dict[str, Any]:
    """Return worker runtime NAT policy diagnostics for N6 egress."""
    try:
        return svc.get_n6_nat_diagnostics()
    except Exception as exc:
        log.exception("N6 NAT diagnostics failed")
        raise HTTPException(500, detail=str(exc)) from exc


@router.get("/nads")
def list_nads(
    namespace: str = settings.default_namespace,
    k8s: K8sService = Depends(get_k8s_service),
) -> list[dict]:
    nads = k8s.list_nads(namespace)
    mec_nads = k8s.list_nads("mec")
    return nads + mec_nads


@router.get("/interfaces")
def list_interfaces(
    namespace: str = settings.default_namespace,
    k8s: K8sService = Depends(get_k8s_service),
) -> list[dict]:
    """Per-NF interface mapping from pod network-status annotations."""
    pods = k8s.core.list_namespaced_pod(namespace=namespace).items
    result: list[dict] = []
    for pod in pods:
        annotation = (pod.metadata.annotations or {}).get(
            "k8s.v1.cni.cncf.io/network-status"
        )
        if not annotation:
            continue
        try:
            networks = json.loads(annotation)
        except json.JSONDecodeError:
            continue
        if not isinstance(networks, list):
            continue

        labels = pod.metadata.labels or {}
        app = labels.get("app", pod.metadata.name)
        ifaces: list[dict] = []
        for net in networks:
            net_name = net.get("name", "").split("/")[-1]
            ifaces.append({
                "name": net_name,
                "label": INTERFACE_LABELS.get(net_name, net_name),
                "interface": net.get("interface", ""),
                "ips": net.get("ips", []),
                "mac": net.get("mac", ""),
                "mtu": net.get("mtu"),
                "default": net.get("default", False),
            })
        result.append({
            "pod": pod.metadata.name,
            "app": app,
            "node": pod.spec.node_name,
            "phase": pod.status.phase if not pod.metadata.deletion_timestamp else "Terminating",
            "interfaces": ifaces,
        })
    return result

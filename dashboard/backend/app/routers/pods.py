from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.models import ConfigMapPayload, PodSummary, RestartRequest, ScaleControllerRequest
from app.services.audit import write_audit
from app.services.amf_cni_service import check_alert, scale_controller
from app.services.k8s_service import K8sService, get_k8s_service

router = APIRouter(prefix="/api/v1", tags=["pods"])


@router.get("/pods", response_model=list[PodSummary])
def list_pods(
    namespace: str = settings.default_namespace,
    k8s: K8sService = Depends(get_k8s_service),
) -> list[PodSummary]:
    return k8s.list_pods(namespace)


@router.post("/deployments/{deployment_name}/restart")
def restart_deployment(
    deployment_name: str,
    payload: RestartRequest,
    k8s: K8sService = Depends(get_k8s_service),
) -> dict[str, str]:
    k8s.restart_deployment(payload.namespace, deployment_name)
    write_audit(
        "deployment.restart",
        {"namespace": payload.namespace, "deployment": deployment_name},
    )
    return {"status": "accepted", "deployment": deployment_name, "namespace": payload.namespace}


@router.get("/pods/amf-cni-alert")
def amf_cni_alert(k8s: K8sService = Depends(get_k8s_service)) -> dict[str, Any]:
    """Get AMF CNI/controller diagnostics used by the Core dashboard."""
    return check_alert(k8s)


@router.post("/pods/amf-controllers/scale")
def scale_amf_controller(
    payload: ScaleControllerRequest,
    k8s: K8sService = Depends(get_k8s_service),
) -> dict[str, Any]:
    try:
        return scale_controller(
            k8s,
            namespace=payload.namespace,
            kind=payload.kind,
            name=payload.name,
            replicas=payload.replicas,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/pods/{pod}/describe")
def describe_pod(
    pod: str,
    namespace: str = settings.default_namespace,
    k8s: K8sService = Depends(get_k8s_service),
) -> dict[str, Any]:
    try:
        p = k8s.core.read_namespaced_pod(name=pod, namespace=namespace)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Pod {pod} not found") from exc

    def _container_status(cs) -> dict:
        state_info = {}
        if cs.state:
            if cs.state.running:
                state_info = {"state": "running", "started_at": str(cs.state.running.started_at or "")}
            elif cs.state.waiting:
                state_info = {"state": "waiting", "reason": cs.state.waiting.reason or "", "message": cs.state.waiting.message or ""}
            elif cs.state.terminated:
                state_info = {"state": "terminated", "reason": cs.state.terminated.reason or "", "exit_code": cs.state.terminated.exit_code}
        return {
            "name": cs.name,
            "ready": cs.ready,
            "restart_count": cs.restart_count,
            "image": cs.image,
            **state_info,
        }

    conditions = []
    for c in (p.status.conditions or []):
        conditions.append({
            "type": c.type, "status": c.status,
            "reason": c.reason or "", "message": c.message or "",
            "last_transition": str(c.last_transition_time or ""),
        })

    init_statuses = [_container_status(cs) for cs in (p.status.init_container_statuses or [])]
    container_statuses = [_container_status(cs) for cs in (p.status.container_statuses or [])]

    events_raw = k8s.core.list_namespaced_event(
        namespace=namespace,
        field_selector=f"involvedObject.name={pod},involvedObject.kind=Pod",
    )
    events = []
    for e in sorted(events_raw.items, key=lambda x: x.last_timestamp or x.event_time or x.metadata.creation_timestamp, reverse=True)[:30]:
        events.append({
            "type": e.type,
            "reason": e.reason,
            "message": e.message,
            "count": e.count,
            "last_seen": str(e.last_timestamp or e.event_time or ""),
            "source": e.source.component if e.source else "",
        })

    annotations = p.metadata.annotations or {}
    network_annotation = annotations.get("k8s.v1.cni.cncf.io/network-status", "")
    networks_requested = annotations.get("k8s.v1.cni.cncf.io/networks", "")

    return {
        "name": p.metadata.name,
        "namespace": p.metadata.namespace,
        "phase": p.status.phase,
        "node": p.spec.node_name,
        "conditions": conditions,
        "init_containers": init_statuses,
        "containers": container_statuses,
        "events": events,
        "networks_requested": networks_requested,
        "network_status": network_annotation,
    }


@router.get("/configmaps/{name}")
def get_configmap(
    name: str,
    namespace: str = settings.default_namespace,
    k8s: K8sService = Depends(get_k8s_service),
) -> dict:
    return k8s.get_configmap(namespace=namespace, name=name)


@router.put("/configmaps/{name}")
def update_configmap(
    name: str,
    payload: ConfigMapPayload,
    namespace: str = settings.default_namespace,
    k8s: K8sService = Depends(get_k8s_service),
) -> dict[str, str]:
    if not settings.allow_configmap_write:
        raise HTTPException(status_code=403, detail="ConfigMap write is disabled by policy")

    k8s.apply_configmap(namespace=namespace, name=name, data=payload.data)
    for dep in payload.restart_deployments:
        k8s.restart_deployment(namespace=namespace, deployment_name=dep)

    write_audit(
        "configmap.update",
        {
            "namespace": namespace,
            "configmap": name,
            "restart_deployments": payload.restart_deployments,
        },
    )
    return {"status": "applied", "name": name, "namespace": namespace}

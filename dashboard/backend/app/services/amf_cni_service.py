"""AMF CNI/stuck-controller diagnostics and actions.

Watches for FailedCreatePodSandBox events with "file exists", plus duplicate/stuck
AMF controllers. Exposes data for dashboard-driven scale actions.
"""

import logging
from typing import Any, Callable

from app.services.audit import write_audit
from app.services.k8s_service import K8sService

log = logging.getLogger(__name__)

NS = "5g"
AMF_LABEL = "app=amf"
REASON = "FailedCreatePodSandBox"
FILE_EXISTS = "file exists"


def _event_ts(event: Any) -> str:
    return str(event.last_timestamp or event.event_time or event.metadata.creation_timestamp or "")


def check_alert(k8s: K8sService) -> dict[str, Any]:
    """Return AMF networking/controller alert context for dashboard UI."""
    try:
        amf_pods = k8s.core.list_namespaced_pod(namespace=NS, label_selector=AMF_LABEL).items
        pod_by_name = {p.metadata.name: p for p in amf_pods}

        events = k8s.core.list_namespaced_event(
            namespace=NS,
            field_selector="involvedObject.kind=Pod",
        )
        file_exists_events: list[dict[str, Any]] = []
        for e in events.items:
            if e.reason != REASON or not e.message:
                continue
            if FILE_EXISTS.lower() not in e.message.lower():
                continue
            obj = e.involved_object
            if not obj or obj.kind != "Pod":
                continue
            pod = pod_by_name.get(obj.name)
            if not pod or (pod.metadata.labels or {}).get("app") != "amf":
                continue
            file_exists_events.append({
                "pod_name": obj.name,
                "message": e.message,
                "reason": e.reason,
                "last_seen": _event_ts(e),
                "count": int(e.count or 1),
            })

        deployments = k8s.apps.list_namespaced_deployment(namespace=NS, label_selector=AMF_LABEL).items
        replicasets = k8s.apps.list_namespaced_replica_set(namespace=NS, label_selector=AMF_LABEL).items

        dep_rows = [{
            "kind": "deployment",
            "name": d.metadata.name,
            "desired": int(d.spec.replicas or 0),
            "ready": int(d.status.ready_replicas or 0),
            "available": int(d.status.available_replicas or 0),
        } for d in deployments]
        rs_rows = [{
            "kind": "replicaset",
            "name": rs.metadata.name,
            "desired": int(rs.spec.replicas or 0),
            "ready": int(rs.status.ready_replicas or 0),
            "available": int(rs.status.available_replicas or 0),
        } for rs in replicasets]

        active_rs = [r for r in rs_rows if r["desired"] > 0]
        stuck_pods = [{
            "name": p.metadata.name,
            "phase": p.status.phase,
            "node": p.spec.node_name,
        } for p in amf_pods if p.status.phase != "Running"]

        reasons: list[str] = []
        if file_exists_events:
            reasons.append("failed_sandbox_file_exists")
        if len(active_rs) > 1:
            reasons.append("duplicate_active_replicasets")
        if stuck_pods:
            reasons.append("stuck_amf_pods")

        return {
            "active": bool(reasons),
            "reasons": reasons,
            "events": sorted(file_exists_events, key=lambda x: x["last_seen"], reverse=True)[:10],
            "stuck_pods": stuck_pods,
            "controllers": {
                "deployments": dep_rows,
                "replicasets": rs_rows,
            },
            "summary": {
                "file_exists_events": len(file_exists_events),
                "active_replicasets": len(active_rs),
                "stuck_pods": len(stuck_pods),
            },
        }
    except Exception as exc:
        log.debug("AMF CNI alert check failed: %s", exc)
    return {"active": False}


def scale_controller(
    k8s: K8sService,
    *,
    kind: str,
    name: str,
    replicas: int,
    namespace: str = NS,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Scale AMF deployment/replicaset from dashboard action."""
    target = max(0, int(replicas))
    normalized = (kind or "").strip().lower()
    if normalized not in {"deployment", "replicaset"}:
        raise ValueError(f"Unsupported controller kind: {kind}")

    if on_progress:
        on_progress(f"Scaling {normalized} {name} to {target}")
    if normalized == "deployment":
        k8s.scale_deployment(namespace=namespace, name=name, replicas=target)
    else:
        k8s.apps.patch_namespaced_replica_set_scale(
            name=name,
            namespace=namespace,
            body={"spec": {"replicas": target}},
        )

    write_audit(
        "amf_controller.scale",
        {"namespace": namespace, "kind": normalized, "name": name, "replicas": target},
    )
    return {"status": "accepted", "kind": normalized, "name": name, "namespace": namespace, "replicas": target}

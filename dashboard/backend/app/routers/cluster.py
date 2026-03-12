from fastapi import APIRouter, Depends

from app.config import settings
from app.models import (
    ClusterStats,
    ClusterSummary,
    NfInstance,
    NfStatusResponse,
    PodSummary,
)
from app.services.k8s_service import K8sService, get_k8s_service

router = APIRouter(prefix="/api/v1", tags=["cluster"])

NF_TYPES = [
    "amf", "smf", "upf", "nrf", "udm", "udr",
    "ausf", "pcf", "nssf", "bsf", "gnb", "ue",
]

CONTROL_PLANE_NFS = {"amf", "smf", "nrf", "udm", "udr", "ausf", "pcf", "bsf", "nssf"}
USER_PLANE_NFS = {"upf"}
DATA_NFS = {"mongo", "mongodb"}


def infer_nf_type(name: str, labels: dict[str, str]) -> str:
    app = labels.get("app", "")
    value = (app or name).lower()
    for kind in NF_TYPES:
        if kind in value:
            return kind
    if "mongo" in value:
        return "mongodb"
    return "unknown"


def categorize_nf(nf_type: str) -> str:
    if nf_type in CONTROL_PLANE_NFS:
        return "control_plane"
    if nf_type in USER_PLANE_NFS:
        return "user_plane"
    if nf_type in DATA_NFS:
        return "data"
    return "other"


def _deduplicate_pods(pods: list[PodSummary]) -> list[PodSummary]:
    """During rollouts K8s keeps the old pod (Terminating) alongside the new one.
    Group by deployment and drop Terminating pods when a replacement exists."""
    dep_groups: dict[str, list[PodSummary]] = {}
    no_dep: list[PodSummary] = []

    for pod in pods:
        if pod.deployment:
            dep_groups.setdefault(pod.deployment, []).append(pod)
        else:
            no_dep.append(pod)

    active: list[PodSummary] = list(no_dep)
    for group in dep_groups.values():
        non_terminating = [p for p in group if p.phase != "Terminating"]
        if non_terminating:
            active.extend(non_terminating)
        else:
            active.append(group[0])
    return active


@router.get("/cluster/summary", response_model=ClusterSummary)
def get_cluster_summary(
    k8s: K8sService = Depends(get_k8s_service),
) -> ClusterSummary:
    nodes = k8s.list_nodes()
    pods = k8s.list_pods(settings.default_namespace)
    active = _deduplicate_pods(pods)
    running = sum(1 for p in active if p.phase == "Running")
    pending = sum(1 for p in active if p.phase in ("Pending", "ContainerCreating"))
    failed = sum(1 for p in active if p.phase == "Failed")
    return ClusterSummary(
        nodes=nodes,
        stats=ClusterStats(
            total_pods=len(active),
            running=running,
            pending=pending,
            failed=failed,
        ),
    )


@router.get("/nf/status", response_model=NfStatusResponse)
def get_nf_status(
    k8s: K8sService = Depends(get_k8s_service),
) -> NfStatusResponse:
    pods = k8s.list_pods(settings.default_namespace)
    active = _deduplicate_pods(pods)
    result = NfStatusResponse()
    for pod in active:
        nf_type = infer_nf_type(pod.name, pod.labels)
        category = categorize_nf(nf_type)
        instance = NfInstance(
            nf_type=nf_type,
            category=category,
            name=pod.name,
            phase=pod.phase,
            restarts=pod.restarts,
            node=pod.node,
            pod_ip=pod.pod_ip,
            start_time=pod.start_time,
            deployment=pod.deployment,
            containers=pod.containers,
        )
        getattr(result, category).append(instance)
    return result

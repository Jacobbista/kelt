"""Generic Kubernetes inventory endpoints used by the dashboard "Kubernetes" page.

Read-only views on cluster-wide objects that are not specific to the 5G core
(namespaces, PVCs, storage classes, services, events, node details). 5G-core
oriented views live under /api/v1/cluster/summary and /api/v1/nf/status.
"""

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.services.k8s_service import K8sService, get_k8s_service

router = APIRouter(prefix="/api/v1/k8s", tags=["kubernetes"])


@router.get("/namespaces")
def list_namespaces(
    k8s: K8sService = Depends(get_k8s_service),
) -> list[dict[str, Any]]:
    return k8s.list_namespaces()


@router.get("/nodes")
def list_nodes_detailed(
    k8s: K8sService = Depends(get_k8s_service),
) -> list[dict[str, Any]]:
    return k8s.describe_nodes()


@router.get("/pvcs")
def list_pvcs(
    namespace: str | None = Query(default=None, description="Filter by namespace. Omit for all."),
    k8s: K8sService = Depends(get_k8s_service),
) -> list[dict[str, Any]]:
    return k8s.list_pvcs(namespace=namespace)


@router.get("/storageclasses")
def list_storage_classes(
    k8s: K8sService = Depends(get_k8s_service),
) -> list[dict[str, Any]]:
    return k8s.list_storage_classes()


@router.get("/services")
def list_services(
    namespace: str | None = Query(default=None),
    k8s: K8sService = Depends(get_k8s_service),
) -> list[dict[str, Any]]:
    return k8s.list_services(namespace=namespace)


@router.get("/events")
def list_events(
    namespace: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    k8s: K8sService = Depends(get_k8s_service),
) -> list[dict[str, Any]]:
    return k8s.list_events(namespace=namespace, limit=limit)

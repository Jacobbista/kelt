"""Node disk usage and reclaim actions.

Reading is viewer-visible (knowing the disk is filling is diagnostic information,
the same reasoning as the metrics and log views). Every action that frees space
is admin-only and lives on the write router.
See docs/security/iam.md for the per-route matrix.
"""

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.services.k8s_service import K8sService, get_k8s_service
from app.services.storage_service import StorageService

read_router = APIRouter(prefix="/api/v1/storage", tags=["storage"])
write_router = APIRouter(prefix="/api/v1/storage", tags=["storage"])


@read_router.get("")
def storage_usage(
    refresh: bool = Query(False, description="Re-walk the filesystem instead of using the cache"),
) -> dict[str, Any]:
    return StorageService().usage(refresh=refresh)


@read_router.get("/preview")
def storage_preview() -> dict[str, Any]:
    """Estimated saving per reclaim action. Read-only, so viewer-visible."""
    return StorageService().preview()


@write_router.post("/prune-images")
def prune_images() -> dict[str, Any]:
    return StorageService().prune_images()


@write_router.post("/vacuum-journal")
def vacuum_journal() -> dict[str, Any]:
    return StorageService().vacuum_journal()


@write_router.post("/registry-gc")
def registry_gc(
    dry_run: bool = Query(True, description="Report what would be deleted without touching the store"),
    k8s: K8sService = Depends(get_k8s_service),
) -> dict[str, Any]:
    return StorageService(k8s=k8s).garbage_collect_registry(dry_run=dry_run)

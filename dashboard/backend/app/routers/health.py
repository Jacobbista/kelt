from datetime import datetime, timezone

from fastapi import APIRouter

from app.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe. Unauthenticated (browser hits it before login,
    watchdog polls it). Returns only the status and server clock, no
    internal URLs.
    """
    now = datetime.now(timezone.utc)
    return {
        "status": "ok",
        "server_time_utc": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
    }


@router.get("/api/v1/cluster/info")
def cluster_info() -> dict[str, str]:
    """Runtime metadata for the dashboard shell (mode + runtime source).

    Trivial info, kept unauthenticated so the shell can render before the
    OIDC login redirect resolves. Anything sensitive lives behind the
    viewer-or-admin guard on the rest of /api/v1/cluster/*.
    """
    return {
        "mode": settings.mode,
        "runtime_source": settings.runtime_source,
    }

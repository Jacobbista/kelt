from datetime import datetime, timezone

from fastapi import APIRouter
from app.config import settings

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict[str, str]:
    now = datetime.now(timezone.utc)
    return {
        "status": "ok",
        "mode": settings.mode,
        "runtime_source": settings.runtime_source,
        "server_time_utc": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
    }

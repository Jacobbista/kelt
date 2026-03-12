from fastapi import APIRouter
from app.config import settings

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "mode": settings.mode,
        "runtime_source": settings.runtime_source,
    }

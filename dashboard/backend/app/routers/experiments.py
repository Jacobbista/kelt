from fastapi import APIRouter

router = APIRouter(prefix="/api/v1", tags=["experiments"])


@router.post("/experiments/run")
def run_experiment() -> dict[str, str]:
    # Module 4 placeholder. The schema is intentionally simple to keep compatibility later.
    return {"status": "not_implemented", "message": "Experiment automation will be implemented in Module 4."}


@router.post("/snapshot/create")
def create_snapshot() -> dict[str, str]:
    # Module 4 placeholder.
    return {"status": "not_implemented", "message": "Snapshot bundle generation will be implemented in Module 4."}

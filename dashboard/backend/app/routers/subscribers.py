import logging
import os
import subprocess
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.services.audit import write_audit
from app.services.mongo_service import MongoService, get_mongo_service
from app.services.subscriber_schema import SubscriberSchemaError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/subscribers", tags=["subscribers"])

ANSIBLE_DIR = "/home/vagrant/ansible-ro"
ANSIBLE_CFG = f"{ANSIBLE_DIR}/ansible.cfg"
ANSIBLE_PLAYBOOK_BIN = "/home/vagrant/.local/bin/ansible-playbook"
PHASE5_PLAYBOOK = f"{ANSIBLE_DIR}/phases/05-5g-core/playbook.yml"


@router.get("")
def list_subscribers(mongo: MongoService = Depends(get_mongo_service)) -> list[dict[str, Any]]:
    return mongo.list_subscribers()


@router.get("/{imsi}")
def get_subscriber(imsi: str, mongo: MongoService = Depends(get_mongo_service)) -> dict[str, Any]:
    sub = mongo.get_subscriber(imsi)
    if sub is None:
        raise HTTPException(status_code=404, detail=f"Subscriber {imsi} not found")
    return sub


@router.post("")
def create_subscriber(
    payload: dict[str, Any],
    mongo: MongoService = Depends(get_mongo_service),
) -> dict[str, Any]:
    if "imsi" not in payload:
        raise HTTPException(status_code=400, detail="imsi is required")
    try:
        result = mongo.create_subscriber(payload)
    except SubscriberSchemaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    write_audit("subscriber.create", {"imsi": payload["imsi"]})
    return result


@router.put("/{imsi}")
def update_subscriber(
    imsi: str,
    payload: dict[str, Any],
    mongo: MongoService = Depends(get_mongo_service),
) -> dict[str, Any]:
    try:
        result = mongo.update_subscriber(imsi, payload)
    except SubscriberSchemaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail=f"Subscriber {imsi} not found")
    write_audit("subscriber.update", {"imsi": imsi})
    return result


@router.delete("/{imsi}")
def delete_subscriber(
    imsi: str,
    mongo: MongoService = Depends(get_mongo_service),
) -> dict[str, str]:
    if not mongo.delete_subscriber(imsi):
        raise HTTPException(status_code=404, detail=f"Subscriber {imsi} not found")
    write_audit("subscriber.delete", {"imsi": imsi})
    return {"status": "deleted", "imsi": imsi}


@router.post("/import")
def import_subscribers(
    payload: dict[str, Any],
    mongo: MongoService = Depends(get_mongo_service),
) -> dict[str, Any]:
    subs = payload.get("subscribers", [])
    if not isinstance(subs, list):
        raise HTTPException(status_code=400, detail="Expected { subscribers: [...] }")
    created = 0
    for sub in subs:
        if "imsi" in sub:
            mongo.create_subscriber(sub)
            created += 1
    # create_subscriber already syncs the snapshot after each upsert, but we
    # force one more at the end to guarantee the ConfigMap reflects the final
    # state even if an earlier sync failed transiently.
    mongo.sync_snapshot()
    write_audit("subscriber.import", {"count": created})
    return {"status": "imported", "count": created}


@router.post("/sync")
def sync_snapshot(
    mongo: MongoService = Depends(get_mongo_service),
) -> dict[str, Any]:
    """Force the subscribers-snapshot ConfigMap to reflect the current MongoDB state.

    Useful after an Ansible playbook re-run (which may have bulk-imported subscribers
    directly into Mongo without going through the dashboard API) to re-align the
    snapshot used for MongoDB pod restart reconcile.
    """
    ok = mongo.sync_snapshot()
    count = len(mongo.list_subscribers())
    write_audit("subscriber.snapshot_sync", {"ok": ok, "count": count})
    if not ok:
        raise HTTPException(
            status_code=502,
            detail="Could not write subscriber snapshot ConfigMap (check backend logs)",
        )
    return {"status": "ok", "count": count}


@router.post("/init")
def init_subscribers(
    mongo: MongoService = Depends(get_mongo_service),
) -> dict[str, Any]:
    """Run the Ansible subscriber_import phase to seed subscribers from subscribers.json."""
    cmd = [
        ANSIBLE_PLAYBOOK_BIN, PHASE5_PLAYBOOK,
        "--tags", "subscribers",
    ]
    env = {**os.environ, "ANSIBLE_CONFIG": ANSIBLE_CFG}

    log.info("Running subscriber init: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=120, cwd=ANSIBLE_DIR, env=env, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="Playbook timed out") from exc

    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        log.error("Subscriber init failed (rc=%d):\n%s", proc.returncode, output[-2000:])
        raise HTTPException(
            status_code=500,
            detail=f"Playbook failed (rc={proc.returncode}): {output[-800:]}",
        )

    # Align the snapshot ConfigMap with what the playbook just wrote into Mongo.
    mongo.sync_snapshot()
    write_audit("subscriber.init_playbook", {})
    log.info("Subscriber init completed")
    return {"status": "ok", "detail": "subscriber_import playbook completed"}

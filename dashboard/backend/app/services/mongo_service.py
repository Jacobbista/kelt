import logging
from typing import Any

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

from app.config import settings
from app.services.subscriber_schema import normalize_subscriber
from app.services.subscriber_snapshot import SubscriberSnapshotService

log = logging.getLogger(__name__)

_client: MongoClient | None = None


def _get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(settings.mongodb_url, serverSelectionTimeoutMS=3000)
    return _client


class MongoService:
    def __init__(self, snapshot: SubscriberSnapshotService | None = None) -> None:
        self.client = _get_client()
        self.db = self.client.get_database("open5gs")
        self.subscribers = self.db["subscribers"]
        # Dashboard-side UE customizations (display nickname, icon). Lives in
        # the same MongoDB as the 5G core so it survives dashboard restarts
        # without introducing a second stateful service or ConfigMap.
        self.ue_personalizations = self.db["ue_personalizations"]
        self._snapshot = snapshot

    @property
    def snapshot(self) -> SubscriberSnapshotService:
        if self._snapshot is None:
            self._snapshot = SubscriberSnapshotService()
        return self._snapshot

    def ping(self) -> bool:
        try:
            self.client.admin.command("ping")
            return True
        except ConnectionFailure:
            return False

    def list_subscribers(self) -> list[dict[str, Any]]:
        docs = list(self.subscribers.find({}, {"_id": 0}))
        return docs

    def get_subscriber(self, imsi: str) -> dict[str, Any] | None:
        return self.subscribers.find_one({"imsi": imsi}, {"_id": 0})

    def create_subscriber(self, data: dict[str, Any]) -> dict[str, Any]:
        data = normalize_subscriber(data)
        self.subscribers.update_one(
            {"imsi": data["imsi"]},
            {"$set": data},
            upsert=True,
        )
        result = self.get_subscriber(data["imsi"]) or data
        self._sync_snapshot()
        return result

    def update_subscriber(self, imsi: str, data: dict[str, Any]) -> dict[str, Any] | None:
        existing = self.subscribers.find_one({"imsi": imsi})
        if existing is None:
            return None
        existing.pop("_id", None)
        merged = {**existing, **{k: v for k, v in data.items() if k != "imsi"}}
        merged["imsi"] = imsi
        normalized = normalize_subscriber(merged)
        self.subscribers.update_one(
            {"imsi": imsi},
            {"$set": normalized},
        )
        result = self.get_subscriber(imsi)
        self._sync_snapshot()
        return result

    def delete_subscriber(self, imsi: str) -> bool:
        result = self.subscribers.delete_one({"imsi": imsi})
        if result.deleted_count > 0:
            self._sync_snapshot()
            return True
        return False

    def sync_snapshot(self) -> bool:
        """Force-write the current subscriber list into the snapshot ConfigMap."""
        return self._sync_snapshot()

    def _sync_snapshot(self) -> bool:
        """Mirror the full subscriber list into the snapshot ConfigMap.

        Best-effort: returns False on failure but never raises, so a transient
        Kubernetes API issue cannot break subscriber CRUD.
        """
        try:
            subs = self.list_subscribers()
        except Exception:
            log.exception("failed to enumerate subscribers for snapshot sync")
            return False
        return self.snapshot.write(subs)

    # ── UE personalizations (dashboard-only) ────────────────────

    def list_ue_personalizations(self) -> list[dict[str, Any]]:
        return list(self.ue_personalizations.find({}, {"_id": 0}))

    def get_ue_personalizations_map(self) -> dict[str, dict[str, Any]]:
        """Return {imsi: personalization} for O(1) enrichment lookups."""
        try:
            return {doc["imsi"]: doc for doc in self.list_ue_personalizations() if doc.get("imsi")}
        except Exception:
            log.exception("failed to fetch UE personalizations")
            return {}

    def upsert_ue_personalization(
        self,
        imsi: str,
        nickname: str | None = None,
        icon: str | None = None,
        image: str | None = None,
    ) -> dict[str, Any]:
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        update: dict[str, Any] = {"imsi": imsi, "updated_at": now}
        if nickname is not None:
            update["nickname"] = nickname.strip() or None
        if icon is not None:
            update["icon"] = icon.strip() or None
        if image is not None:
            # Image is a validated data URL; an empty string clears the field.
            update["image"] = image.strip() or None
        self.ue_personalizations.update_one(
            {"imsi": imsi}, {"$set": update}, upsert=True,
        )
        return self.ue_personalizations.find_one({"imsi": imsi}, {"_id": 0}) or update

    def delete_ue_personalization(self, imsi: str) -> bool:
        return self.ue_personalizations.delete_one({"imsi": imsi}).deleted_count > 0


def get_mongo_service() -> MongoService:
    return MongoService()

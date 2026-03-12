import logging
from typing import Any

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

from app.config import settings

log = logging.getLogger(__name__)

_client: MongoClient | None = None


def _get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(settings.mongodb_url, serverSelectionTimeoutMS=3000)
    return _client


class MongoService:
    def __init__(self) -> None:
        self.client = _get_client()
        self.db = self.client.get_database("open5gs")
        self.subscribers = self.db["subscribers"]

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
        data.pop("_id", None)
        self.subscribers.update_one(
            {"imsi": data["imsi"]},
            {"$set": data},
            upsert=True,
        )
        return self.get_subscriber(data["imsi"]) or data

    def update_subscriber(self, imsi: str, data: dict[str, Any]) -> dict[str, Any] | None:
        data.pop("_id", None)
        data.pop("imsi", None)
        result = self.subscribers.update_one({"imsi": imsi}, {"$set": data})
        if result.matched_count == 0:
            return None
        return self.get_subscriber(imsi)

    def delete_subscriber(self, imsi: str) -> bool:
        result = self.subscribers.delete_one({"imsi": imsi})
        return result.deleted_count > 0


def get_mongo_service() -> MongoService:
    return MongoService()

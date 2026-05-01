"""Keep the subscribers-snapshot ConfigMap in sync with MongoDB.

The MongoDB pod mounts this ConfigMap and, on startup, reconciles its
`subscribers` collection against it: IMSIs in the snapshot get upserted,
IMSIs in Mongo that are not in the snapshot get deleted.

This makes the ConfigMap the durable source of truth for dashboard-driven
subscriber changes, so the user-plane survives even a complete loss of the
MongoDB PVC (fresh deploy, disk reset, etc.) without needing to re-run the
Ansible subscriber_import role.

See docs/architecture/subscriber-persistence.md
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from kubernetes.client.exceptions import ApiException

from app.config import settings
from app.services.k8s_service import K8sService

log = logging.getLogger(__name__)


def _sanitize(subs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip Mongo-internal fields and ensure a stable shape for the snapshot."""
    out: list[dict[str, Any]] = []
    for sub in subs:
        if not isinstance(sub, dict):
            continue
        clean = {k: v for k, v in sub.items() if k != "_id"}
        if "imsi" not in clean:
            continue
        clean["imsi"] = str(clean["imsi"])
        out.append(clean)
    return out


class SubscriberSnapshotService:
    """Write the current subscriber list into a ConfigMap.

    All operations are best-effort: a failure to write the ConfigMap must
    never break a dashboard API call, because MongoDB remains authoritative
    for the running session. Failures are logged for observability.
    """

    def __init__(self, k8s: K8sService | None = None) -> None:
        self._k8s = k8s
        self.namespace = settings.subscriber_snapshot_namespace
        self.name = settings.subscriber_snapshot_configmap
        self.key = settings.subscriber_snapshot_key

    @property
    def k8s(self) -> K8sService:
        if self._k8s is None:
            self._k8s = K8sService()
        return self._k8s

    def write(self, subscribers: Iterable[dict[str, Any]]) -> bool:
        """Serialise the given subscriber list into the snapshot ConfigMap.

        Returns True on success, False if the ConfigMap could not be written.
        """
        payload = {"subscribers": _sanitize(subscribers)}
        data = {self.key: json.dumps(payload, ensure_ascii=False, sort_keys=True)}
        try:
            self.k8s.apply_configmap(self.namespace, self.name, data)
            log.debug(
                "subscriber snapshot updated (namespace=%s cm=%s count=%d)",
                self.namespace, self.name, len(payload["subscribers"]),
            )
            return True
        except ApiException as exc:
            log.warning(
                "failed to update subscriber snapshot ConfigMap %s/%s (k8s api error %s)",
                self.namespace, self.name, exc.status,
            )
        except Exception:
            log.exception(
                "unexpected error updating subscriber snapshot ConfigMap %s/%s",
                self.namespace, self.name,
            )
        return False

    def exists(self) -> bool:
        try:
            self.k8s.get_configmap(self.namespace, self.name)
            return True
        except ApiException as exc:
            if exc.status == 404:
                return False
            log.warning(
                "unable to check subscriber snapshot ConfigMap %s/%s (status=%s)",
                self.namespace, self.name, exc.status,
            )
            return False
        except Exception:
            log.exception(
                "unexpected error checking subscriber snapshot ConfigMap %s/%s",
                self.namespace, self.name,
            )
            return False


def get_snapshot_service() -> SubscriberSnapshotService:
    return SubscriberSnapshotService()

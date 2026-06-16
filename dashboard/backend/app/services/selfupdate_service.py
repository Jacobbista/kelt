"""Self-update awareness for the dashboard's own components (frontend, docs).

Both deploy `:latest`, so tag comparison is useless; instead compare the running
pod's image digest with the registry's current :latest digest. When the registry
is unreachable (offline / air-gapped) the status is "unknown" and nothing breaks.
The update action is a targeted rollout restart of just that component (re-pulls
:latest) — no full phase re-run. Mirrors the NF version-update pattern.
"""

import json
import logging
import urllib.request
from typing import Any

from app.services.k8s_service import K8sService

log = logging.getLogger(__name__)

# Dashboard self-managed components. image is the :latest ref these deploy.
COMPONENTS = {
    "dashboard-frontend": {
        "namespace": "dashboard",
        "label": "app=dashboard-frontend",
        "image": "ghcr.io/jacobbista/dashboard-frontend:latest",
        "display": "Dashboard frontend",
    },
    "dashboard-docs": {
        "namespace": "dashboard",
        "label": "app=dashboard-docs",
        "image": "ghcr.io/jacobbista/kelt-docs:latest",
        "display": "Documentation",
    },
}

_MANIFEST_ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
])


def _extract_sha(image_id: str) -> str | None:
    # imageID looks like "ghcr.io/owner/repo@sha256:...", possibly with a
    # "docker-pullable://" prefix. We want the sha256:... part.
    if not image_id or "sha256:" not in image_id:
        return None
    return "sha256:" + image_id.split("sha256:", 1)[1].strip()


def _parse_ref(image_ref: str) -> tuple[str, str] | None:
    # ghcr.io/owner/repo:tag -> ("owner/repo", "tag"). Only ghcr.io is handled.
    if not image_ref.startswith("ghcr.io/"):
        return None
    rest = image_ref[len("ghcr.io/"):]
    repo, _, tag = rest.partition(":")
    return repo, (tag or "latest")


class SelfUpdateService:
    def __init__(self, k8s: K8sService) -> None:
        self.k8s = k8s

    def _deployed_digest(self, namespace: str, label: str) -> str | None:
        try:
            pods = self.k8s.core.list_namespaced_pod(namespace=namespace, label_selector=label)
            for p in pods.items:
                for cs in (p.status.container_statuses or []):
                    sha = _extract_sha(cs.image_id or "")
                    if sha:
                        return sha
        except Exception as exc:
            log.debug("deployed_digest %s/%s: %s", namespace, label, exc)
        return None

    def _registry_digest(self, image_ref: str) -> str | None:
        parsed = _parse_ref(image_ref)
        if not parsed:
            return None
        repo, tag = parsed
        try:
            # GHCR requires a (anonymous, for public repos) bearer token.
            tok_url = f"https://ghcr.io/token?service=ghcr.io&scope=repository:{repo}:pull"
            with urllib.request.urlopen(tok_url, timeout=8) as r:
                token = json.loads(r.read().decode()).get("token")
            if not token:
                return None
            req = urllib.request.Request(
                f"https://ghcr.io/v2/{repo}/manifests/{tag}",
                method="HEAD",
                headers={"Authorization": f"Bearer {token}", "Accept": _MANIFEST_ACCEPT},
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                return r.headers.get("Docker-Content-Digest")
        except Exception as exc:
            log.info("registry_digest %s: %s (offline?)", image_ref, exc)
            return None

    def status(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name, c in COMPONENTS.items():
            deployed = self._deployed_digest(c["namespace"], c["label"])
            registry = self._registry_digest(c["image"])
            if registry is None:
                state = "unknown"  # registry unreachable (offline) — cannot tell
            elif deployed is None:
                state = "not-deployed"
            elif deployed == registry:
                state = "up-to-date"
            else:
                state = "update-available"
            out.append({
                "name": name,
                "display": c["display"],
                "image": c["image"],
                "deployed_digest": deployed,
                "registry_digest": registry,
                "state": state,
            })
        return out

    def update(self, name: str) -> dict[str, Any]:
        c = COMPONENTS.get(name)
        if not c:
            raise ValueError(f"Unknown component '{name}' (one of {sorted(COMPONENTS)})")
        # Targeted rollout: re-pull :latest for just this component (no full phase).
        self.k8s.restart_deployment(c["namespace"], name)
        return {"status": "rolling-out", "name": name, "namespace": c["namespace"]}

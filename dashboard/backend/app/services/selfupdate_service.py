"""Self-update awareness for the dashboard's own components (frontend, docs).

The two components are versioned differently, so they are checked differently:

- The frontend is PINNED to a semver tag (baseline in all.yml, bumped with the
  git tag that triggers CI). Comparison is tag-based against the newest semver
  on ghcr within the deployed major, and updating patches the Deployment image.
  That makes the running version explicit and `kubectl rollout undo` meaningful.
- The docs image is CONTINUOUSLY PUBLISHED (any push touching docs/** rebuilds
  it) and carries no semver, so it stays on `:latest`. Tag comparison is useless
  there; compare the running pod's image digest with the registry digest for
  `:latest`, and update with a rollout restart that re-pulls it.

When the registry is unreachable (offline / air-gapped) the status is "unknown"
and nothing breaks. See docs/development/contributing.md "Publishing images".
"""

import json
import logging
import urllib.request
from typing import Any

from app.services.k8s_service import K8sService
# Reused rather than duplicated: the northbound console already resolves "newest
# semver on ghcr within this major" against the same registry.
from app.services.northbound_service import _ghcr_latest_in_major, _semver_key

log = logging.getLogger(__name__)

# Dashboard self-managed components. "mode" selects how a newer build is detected:
# "tag" for a pinned semver image, "digest" for a continuously published :latest.
# The repo is read from the running Deployment, so no image ref is hardcoded here.
COMPONENTS = {
    "dashboard-frontend": {
        "namespace": "dashboard",
        "label": "app=dashboard-frontend",
        "mode": "tag",
        "display": "Dashboard frontend",
    },
    "dashboard-docs": {
        "namespace": "dashboard",
        "label": "app=dashboard-docs",
        "mode": "digest",
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

    @staticmethod
    def _latest_for(repo: str, tag: str) -> str | None:
        """Newest semver on ghcr within the deployed major, or None when the tag is
        not semver (`latest`, `sha-*`) or the registry is unreachable. Staying inside
        the major keeps a breaking release a deliberate act, not a self-update."""
        key = _semver_key(tag)
        if not key:
            return None
        return _ghcr_latest_in_major(repo, key[0])

    def _deployed_image(self, namespace: str, name: str) -> str | None:
        """The image ref the Deployment asks for, which is the pinned intent (a pod
        status reports the resolved digest instead)."""
        try:
            dep = self.k8s.apps.read_namespaced_deployment(name=name, namespace=namespace)
            containers = dep.spec.template.spec.containers or []
            return containers[0].image if containers else None
        except Exception as exc:
            log.debug("deployed_image %s/%s: %s", namespace, name, exc)
            return None

    def status(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name, c in COMPONENTS.items():
            image = self._deployed_image(c["namespace"], name)
            entry: dict[str, Any] = {
                "name": name,
                "display": c["display"],
                "image": image,
                "deployed_digest": None,
                "registry_digest": None,
                "deployed_version": None,
                "latest_version": None,
            }
            if not image:
                entry["state"] = "not-deployed"
                out.append(entry)
                continue

            # A component declared "tag" can still be running a mutable tag: the
            # deployment predates the pin and phase 09 has not re-applied yet. Falling
            # back to digest comparison keeps the panel truthful (and the button
            # working) through that transition instead of reporting "unknown".
            deployed_key = _semver_key(_parse_ref(image)[1]) if _parse_ref(image) else None
            if c["mode"] == "tag" and deployed_key is not None:
                repo, deployed_tag = _parse_ref(image)
                latest = self._latest_for(repo, deployed_tag)
                entry["deployed_version"] = deployed_tag
                entry["latest_version"] = latest
                if latest is None:
                    entry["state"] = "unknown"  # registry unreachable
                elif _semver_key(latest) > deployed_key:
                    entry["state"] = "update-available"
                else:
                    entry["state"] = "up-to-date"
            else:
                deployed = self._deployed_digest(c["namespace"], c["label"])
                registry = self._registry_digest(image)
                entry["deployed_digest"] = deployed
                entry["registry_digest"] = registry
                if registry is None:
                    entry["state"] = "unknown"
                elif deployed is None:
                    entry["state"] = "not-deployed"
                elif deployed == registry:
                    entry["state"] = "up-to-date"
                else:
                    entry["state"] = "update-available"
            out.append(entry)
        return out

    def update(self, name: str) -> dict[str, Any]:
        c = COMPONENTS.get(name)
        if not c:
            raise ValueError(f"Unknown component '{name}' (one of {sorted(COMPONENTS)})")
        ns = c["namespace"]

        image = self._deployed_image(ns, name)
        parsed = _parse_ref(image or "")
        deployed_key = _semver_key(parsed[1]) if parsed else None

        # Digest mode, or a "tag" component still on a mutable tag because phase 09
        # has not applied the pin yet: :latest already points at the new build, so a
        # restart re-pulls it. Same reasoning as the status fallback above.
        if c["mode"] == "digest" or deployed_key is None:
            self.k8s.restart_deployment(ns, name)
            return {"status": "rolling-out", "name": name, "namespace": ns}

        # Pinned image: roll the tag forward. Changing the pod spec is what triggers
        # the rollout, so no restart is needed, and the new tag stays visible in the
        # Deployment. all.yml still holds the committed baseline; a phase re-run
        # keeps whichever is newer.
        repo, deployed_tag = parsed
        latest = self._latest_for(repo, deployed_tag)
        # deployed_key is known to be semver here: the non-semver case returned above.
        if not latest or _semver_key(latest) <= deployed_key:
            return {"status": "up-to-date", "name": name, "namespace": ns,
                    "version": deployed_tag}
        self.k8s.apps.patch_namespaced_deployment(
            name=name, namespace=ns,
            body={"spec": {"template": {"spec": {"containers": [
                {"name": name, "image": f"ghcr.io/{repo}:{latest}"}]}}}},
        )
        return {"status": "rolling-out", "name": name, "namespace": ns,
                "from": deployed_tag, "to": latest}

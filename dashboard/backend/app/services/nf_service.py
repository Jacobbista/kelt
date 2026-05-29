"""NF version management service.

Compares images deployed in Kubernetes against the canonical versions.json
published by the 5g-nf-platform repository, and triggers ansible redeployment
when the operator requests an image update.
"""

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import urllib.request
import urllib.error

from app.services.k8s_service import K8sService

log = logging.getLogger(__name__)

NS = "5g"
VERSIONS_URL = "https://raw.githubusercontent.com/Jacobbista/5g-nf-platform/main/versions.json"
# Cache upstream versions.json for 5 minutes to avoid hammering GitHub API
_versions_cache: dict[str, Any] = {}
_versions_cache_ts: float = 0.0
VERSIONS_CACHE_TTL = 300

ANSIBLE_DIR = "/home/vagrant/ansible-ro"
ANSIBLE_CFG = f"{ANSIBLE_DIR}/ansible.cfg"
ANSIBLE_PLAYBOOK_BIN = "/home/vagrant/.local/bin/ansible-playbook"
PHASE5_PLAYBOOK = f"{ANSIBLE_DIR}/phases/05-5g-core/playbook.yml"
GROUP_VARS = Path(ANSIBLE_DIR) / "group_vars" / "all.yml"

# NF names that map to a running K8s deployment label app=<name>
CORE_NFS = ["amf", "smf", "upf-cloud", "upf-edge", "udm", "udr",
            "nrf", "pcf", "bsf", "nssf", "ausf"]


class NFService:
    def __init__(self, k8s: K8sService) -> None:
        self.k8s = k8s

    def get_deployed_images(self) -> dict[str, str]:
        """Return {nf_name: image_tag} for all running NF pods."""
        result: dict[str, str] = {}
        for nf in CORE_NFS:
            try:
                pods = self.k8s.core.list_namespaced_pod(
                    namespace=NS, label_selector=f"app={nf}",
                )
                if pods.items:
                    image = pods.items[0].spec.containers[0].image
                    result[nf] = image
            except Exception as exc:
                log.debug("get_deployed_images: %s: %s", nf, exc)
        return result

    def get_available_versions(self) -> dict[str, str]:
        """Fetch canonical versions.json from 5g-nf-platform. Cached 5 min."""
        global _versions_cache, _versions_cache_ts
        now = time.monotonic()
        if _versions_cache and (now - _versions_cache_ts) < VERSIONS_CACHE_TTL:
            return _versions_cache
        try:
            req = urllib.request.Request(
                VERSIONS_URL,
                headers={"Accept": "application/json",
                         "User-Agent": "5g-k3s-testbed-dashboard/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            # Strip the _comment key
            data.pop("_comment", None)
            _versions_cache = data
            _versions_cache_ts = now
            return data
        except Exception as exc:
            log.warning("get_available_versions failed: %s", exc)
            return {}

    def compare_versions(self) -> list[dict[str, Any]]:
        """Return per-NF comparison: deployed image, available image, up_to_date."""
        deployed = self.get_deployed_images()
        available = self.get_available_versions()
        out: list[dict[str, Any]] = []
        # upf-cloud and upf-edge share the same "upf" key in versions.json
        key_map = {"upf-cloud": "upf", "upf-edge": "upf"}
        for nf in CORE_NFS:
            vkey = key_map.get(nf, nf)
            dep_image = deployed.get(nf, "")
            avail_image = available.get(vkey, "")
            dep_tag = dep_image.split(":")[-1] if dep_image else ""
            avail_tag = avail_image.split(":")[-1] if avail_image else ""
            out.append({
                "nf":             nf,
                "deployed_image": dep_image,
                "deployed_tag":   dep_tag,
                "available_image": avail_image,
                "available_tag":  avail_tag,
                "up_to_date":     bool(dep_tag and avail_tag and dep_tag == avail_tag),
                "deployed":       bool(dep_image),
            })
        return out

    def update_nf(
        self,
        nf: str,
        tag: str,
        on_progress: Any = None,
    ) -> str:
        """Run ansible phase 05 with a single NF image override.

        Streams output lines via on_progress(line) if provided.
        Returns final stdout+stderr output.
        """
        # Build the nf_images override: merge current all.yml value with the
        # single updated tag so other NFs are unaffected.
        # upf-cloud and upf-edge share the upf key
        nf_key = "upf" if nf in ("upf-cloud", "upf-edge") else nf
        # Construct the image tag from the canonical registry prefix
        registry = "ghcr.io/jacobbista/5g-nf-platform"
        new_image = f"{registry}/{nf_key}:{tag}"
        nf_images_override = json.dumps({nf_key: new_image})

        cmd = [
            ANSIBLE_PLAYBOOK_BIN,
            PHASE5_PLAYBOOK,
            "-e", f"nf_images={nf_images_override}",
        ]
        env = {**os.environ, "ANSIBLE_CONFIG": ANSIBLE_CFG}
        log.info("NF update: %s → %s", nf, new_image)

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=ANSIBLE_DIR, env=env,
        )
        output_lines: list[str] = []
        for line in proc.stdout:  # type: ignore[union-attr]
            output_lines.append(line)
            if on_progress:
                on_progress(line.rstrip())
        proc.wait()
        output = "".join(output_lines)
        if proc.returncode != 0:
            raise RuntimeError(
                f"ansible-playbook failed (rc={proc.returncode})\n{output[-2000:]}"
            )
        return output

"""Edge apps platform (phase 12) business logic.

Deploys an operator's own container image as a pod in the apps namespace and,
when exposed, publishes a Service the front-door reaches at <name>.<base>. This
is intentionally separate from the Northbound (positioning) console: an edge app
is a generic workload, NOT a positioning adapter, so it gets a TCP readiness probe
(arbitrary images need not expose /health) and an optional port-80 Service so the
dynamic front-door route does not need to know the container port.

Reuses the K8sService primitives (upsert_deployment/upsert_service/apply_configmap/
upsert_secret/delete_*) and the DNS-1123 name + image-reference validators from
northbound_service. See docs/architecture/edge-apps.md.
"""

import os
import subprocess
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.k8s_service import K8sService
from app.services.northbound_service import _validate_image, _validate_name
# Reuse the ansible runner coordinates the NF rollout already established.
from app.services.nf_service import ANSIBLE_CFG, ANSIBLE_DIR, ANSIBLE_PLAYBOOK_BIN

# Operator config persisted by testbed-config (synced into the ansible VM at
# /vagrant). Sourced when provisioning so a phase-11 re-run keeps every other
# service's flags instead of reverting them to defaults.
TESTBED_ENV = Path("/vagrant/.testbed.env")
TESTBED_SECRETS = Path("/vagrant/.testbed.secrets")
PHASE12_PLAYBOOK = f"{ANSIBLE_DIR}/phases/12-apps/playbook.yml"
PHASE11_PLAYBOOK = f"{ANSIBLE_DIR}/phases/11-frontdoor/playbook.yml"

MANAGED_BY = "dashboard-apps"
_LABELS_BASE = {"app.kubernetes.io/managed-by": MANAGED_BY}
# Service port the front-door proxies to for an exposed app, so <name>.<base> works
# regardless of the container's own port.
PUBLISHED_PORT = 80


class AppDeployError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)


class AppsService:
    def __init__(self, k8s: K8sService) -> None:
        self.k8s = k8s
        self.ns = settings.apps_namespace

    # ── Public URL ────────────────────────────────────────────────────────────
    def _public_url(self, name: str, exposed: bool) -> str | None:
        if not (exposed and settings.external_base_domain):
            return None
        return f"{settings.external_scheme}://{name}.{settings.external_base_domain}"

    # ── Inventory ───────────────────────────────────────────────────────────--
    def inventory(self) -> dict[str, Any]:
        """Platform state for the Apps console: whether the apps namespace exists
        (the phase 12 deploy ran), where to push images, and the deployed apps. A
        missing namespace means the feature flag is on but the phase has not been
        applied yet, so the page can say so instead of looking 'off'."""
        from kubernetes.client.exceptions import ApiException
        try:
            apps = self.list_apps()
            ready = True
        except ApiException as exc:
            if exc.status != 404:
                raise
            apps, ready = [], False  # namespace not created yet
        return {
            "namespace": self.ns,
            "ready": ready,
            "registry_host": settings.apps_registry_host,
            "apps": apps,
        }

    def list_apps(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        deps = self.k8s.apps.list_namespaced_deployment(
            namespace=self.ns, label_selector=f"app.kubernetes.io/managed-by={MANAGED_BY}"
        ).items
        svc_names = {
            s.metadata.name
            for s in self.k8s.core.list_namespaced_service(namespace=self.ns).items
        }
        for d in deps:
            name = d.metadata.name
            container = (d.spec.template.spec.containers or [None])[0]
            image = container.image if container else None
            desired = d.spec.replicas or 0
            ready = d.status.ready_replicas or 0
            exposed = name in svc_names
            out.append({
                "name": name,
                "image": image,
                "namespace": self.ns,
                "replicas": desired,
                "ready_replicas": ready,
                "ready": desired > 0 and ready >= desired,
                "exposed": exposed,
                "public_url": self._public_url(name, exposed),
                "created": d.metadata.creation_timestamp.isoformat()
                if d.metadata.creation_timestamp else None,
            })
        return sorted(out, key=lambda a: a["name"])

    # ── Deploy / delete ─────────────────────────────────────────────────────--
    def deploy_app(self, req) -> dict[str, Any]:
        _validate_name(req.name)
        _validate_image(req.image)

        cm_name, secret_name = f"{req.name}-config", f"{req.name}-secrets"
        plain = {e.name: e.value for e in req.env if not e.sensitive}
        sensitive = {e.name: e.value for e in req.env if e.sensitive}
        self.k8s.apply_configmap(self.ns, cm_name, plain)
        if sensitive:
            self.k8s.upsert_secret(self.ns, secret_name, sensitive)

        labels = {**_LABELS_BASE, "app": req.name}
        container: dict[str, Any] = {
            "name": req.name,
            "image": req.image,
            "imagePullPolicy": "IfNotPresent",
            "ports": [{"containerPort": req.port, "name": "http"}],
            "envFrom": [
                {"configMapRef": {"name": cm_name, "optional": True}},
                {"secretRef": {"name": secret_name, "optional": True}},
            ],
            "resources": {
                "requests": {"cpu": "50m", "memory": "64Mi"},
                "limits": {"cpu": "1", "memory": "512Mi"},
            },
            # TCP probe, not httpGet /health: an arbitrary app image may not expose
            # a health endpoint, but it must listen on its port to be useful.
            "readinessProbe": {
                "tcpSocket": {"port": req.port},
                "initialDelaySeconds": 5,
                "periodSeconds": 10,
                "failureThreshold": 6,
            },
        }
        pod_spec: dict[str, Any] = {
            "nodeSelector": {"kubernetes.io/hostname": "worker"},
            "containers": [container],
        }
        if req.image_pull_secret:
            pod_spec["imagePullSecrets"] = [{"name": req.image_pull_secret}]

        self.k8s.upsert_deployment(self.ns, {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": req.name, "namespace": self.ns, "labels": labels},
            "spec": {
                "replicas": req.replicas,
                "selector": {"matchLabels": {"app": req.name}},
                "template": {"metadata": {"labels": labels}, "spec": pod_spec},
            },
        })

        if req.expose:
            # Service port 80 -> container port, so the front-door's variable
            # proxy_pass (http://<name>.<ns>.svc) needs no per-app port.
            self.k8s.upsert_service(self.ns, {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": req.name, "namespace": self.ns, "labels": labels},
                "spec": {
                    "selector": {"app": req.name},
                    "ports": [{"port": PUBLISHED_PORT, "targetPort": req.port, "name": "http"}],
                    "type": "ClusterIP",
                },
            })
        else:
            # Toggling expose off: drop any previously published Service.
            self.k8s.delete_service(self.ns, req.name)

        return {
            "status": "deployed",
            "name": req.name,
            "namespace": self.ns,
            "exposed": req.expose,
            "public_url": self._public_url(req.name, req.expose),
        }

    def delete_app(self, name: str) -> dict[str, Any]:
        _validate_name(name)
        self.k8s.delete_deployment(self.ns, name)
        self.k8s.delete_service(self.ns, name)
        # Config objects the deploy created via envFrom.
        self.k8s.delete_secret(self.ns, f"{name}-secrets")
        try:
            self.k8s.core.delete_namespaced_config_map(name=f"{name}-config", namespace=self.ns)
        except Exception:
            pass
        return {"status": "deleted", "name": name, "namespace": self.ns}

    # ── Registry credentials (admin only) ─────────────────────────────────────
    def registry_credentials(self) -> dict[str, Any]:
        """The local-registry basic-auth, shown to admins so they can docker login
        + push. The k8s Secret stores only the bcrypt htpasswd, so the plaintext
        comes from the backend env (set by phase 09 from the same source)."""
        return {
            "host": settings.apps_registry_host,
            "username": settings.apps_registry_username,
            "password": settings.apps_registry_password,
        }

    # ── One-click provision (admin only) ──────────────────────────────────────
    @staticmethod
    def _source_env_file(path: Path) -> dict[str, str]:
        out: dict[str, str] = {}
        if not path.exists():
            return out
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
        return out

    def provision(self, on_progress: Any = None) -> str:
        """Deploy the platform from the dashboard: run phase 12 (registry + apps
        namespace) then phase 11 (front-door route). Sources the operator's
        persisted .testbed.env/.secrets so the phase-11 re-render keeps every other
        service's flags, then forces APPS_ENABLED=true. Streams output via
        on_progress(line). Mirrors nf_service.update_nf."""
        env = {**os.environ, "ANSIBLE_CONFIG": ANSIBLE_CFG}
        # Persisted operator config first (flags for camara/positioning/external
        # access, secrets), so re-running phase 11 does not revert other surfaces.
        env.update(self._source_env_file(TESTBED_ENV))
        env.update(self._source_env_file(TESTBED_SECRETS))
        env["APPS_ENABLED"] = "true"

        output_lines: list[str] = []
        for playbook in (PHASE12_PLAYBOOK, PHASE11_PLAYBOOK):
            if on_progress:
                on_progress(f"=== running {os.path.basename(os.path.dirname(playbook))} ===")
            proc = subprocess.Popen(
                [ANSIBLE_PLAYBOOK_BIN, playbook],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, cwd=ANSIBLE_DIR, env=env,
            )
            for line in proc.stdout:  # type: ignore[union-attr]
                output_lines.append(line)
                if on_progress:
                    on_progress(line.rstrip())
            proc.wait()
            if proc.returncode != 0:
                output = "".join(output_lines)
                raise RuntimeError(
                    f"ansible-playbook {playbook} failed (rc={proc.returncode})\n{output[-2000:]}"
                )
        return "".join(output_lines)

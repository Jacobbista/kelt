"""Business logic for the Northbound (positioning/CAMARA) service-management
console. Wraps K8sService to: inventory the northbound services, read the
engine's adapter registry, deploy custom adapter images (they self-register),
edit the fusion config, and retarget managed images.

v0.6.0 adapter model: the engine is the adapter-registry authority. Adapters
SELF-REGISTER (POST /adapters + heartbeat) and the engine evicts dead ones on
TTL. The console no longer owns ADAPTER_URLS; it reads the live registry from the
engine (GET /adapters via the API-server service proxy) and can force-remove a
stale entry (DELETE /adapters/{name}). See docs/architecture/positioning-adapters.md.
"""

import json
import re
from typing import Any

import httpx

from app.models import DeployEnvVar, DeployImageRequest, FusionConfigPayload
from app.services.k8s_service import K8sService


class GatewayError(Exception):
    """A non-2xx response from the CAMARA gateway, forwarded to the caller as-is."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)

# Namespaces the console manages. Used as a strict allow-list for any create.
NORTHBOUND_NAMESPACES = ["camara", "positioning", "mec"]
POSITIONING_NS = "positioning"
ENGINE_DEPLOYMENT = "positioning-engine"
ENGINE_CONFIGMAP = "positioning-config"
ENGINE_SERVICE = "positioning-engine"
ENGINE_PORT = 8080
CAMARA_NS = "camara"
GATEWAY_SERVICE = "camara-gateway"
GATEWAY_PORT = 8080

# Per-service contract metadata (kind, configurable) cached by (name, image) so
# the 5s inventory poll does not re-fetch /contract every time. Invalidated when
# the image changes.
_CONTRACT_META: dict[tuple, dict] = {}

# Managed deployments that core image rollout may retarget (deployment -> namespace).
# Container name equals the deployment name in every northbound manifest.
MANAGED_DEPLOYMENTS = {
    "camara-gateway": "camara",
    "positioning-engine": "positioning",
    "positioning-demo": "mec",
}

# Consumers whose <field> should point at a deployed adapter of <kind> (the
# adapter image's basename). Used to auto-detect a newly deployed adapter that is
# not yet wired into the consumer and offer a one-click bind (binding_suggestions).
_ADAPTER_BINDINGS = {
    "placement-editor": [
        {"field": "REST_ADAPTER_URL", "kind": "rest-adapter"},
        {"field": "WIFI_POSITIONING_URL", "kind": "wifi-positioning"},
    ],
}


def _image_basename(image: str | None) -> str:
    """ghcr.io/jacobbista/5g-northbound/rest-adapter:0.6.0 -> rest-adapter."""
    return (image or "").rsplit("/", 1)[-1].split("@")[0].split(":")[0]


def _is_file_field(name: str | None, path: str | None) -> bool:
    """A document the operator/portal PROVIDES, by convention named *_FILE with an
    absolute-path value (e.g. the rest-adapter's SCHEMA_FILE). Deliberately NOT
    *_PATH: those (e.g. the engine's BLUEPRINT_SEED_PATH) are paths the service
    READS from a managed/distributed source, not an operator document — those are a
    seed/distribution concern, not a paste-a-file one."""
    return bool(name) and name.endswith("_FILE") and bool(path) and path.startswith("/")

# DNS-1123 label for adapter/service names.
_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
# image[:tag] or image@sha256:...; rejects spaces and shell metacharacters.
_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/:@]{0,255}$")

# The static adapter HTTP contract surfaced in the UI's guidance panel.
MEASUREMENT_SCHEMA = {
    "source": "wifi",
    "frame": "local",
    "x": 11.5,
    "y": 0.0,
    "z": 10.3,
    "accuracy_m": 6.6,
    "confidence": 0.85,
    "timestamp": 1700000000.0,
}

ADAPTER_SKELETON = '''from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

class Measurement(BaseModel):
    source: str = "my-source"
    frame: str = "local"
    x: float; y: float = 0.0; z: float
    accuracy_m: float
    confidence: float
    timestamp: Optional[float] = None

app = FastAPI()
_cache: dict[str, Measurement] = {}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/measurement/{device_id}", response_model=Measurement)
async def get_measurement(device_id: str):
    m = _cache.get(device_id)
    if m is None:
        raise HTTPException(404)
    return m
'''


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise ValueError(f"Invalid name '{name}': must be a DNS-1123 label (lowercase alphanumeric and -)")


def _validate_image(image: str) -> None:
    if not _IMAGE_RE.match(image):
        raise ValueError(f"Invalid image reference '{image}'")


class NorthboundService:
    def __init__(self, k8s: K8sService) -> None:
        self.k8s = k8s

    # ── Inventory ────────────────────────────────────────────────────────────
    def inventory(self) -> dict[str, Any]:
        services: list[dict[str, Any]] = []
        for ns in NORTHBOUND_NAMESPACES:
            try:
                deps = self.k8s.apps.list_namespaced_deployment(namespace=ns).items
            except Exception:
                continue
            pods = {p.name: p for p in self.k8s.list_pods(ns)}
            # NodePort per service (by same-name Service) so the UI can show where
            # each surface is reachable and link to it. ClusterIP-only services
            # (engine, mock) have no nodePort and are internal.
            node_ports: dict[str, int] = {}
            try:
                for svc in self.k8s.core.list_namespaced_service(namespace=ns).items:
                    if svc.spec.type == "NodePort":
                        np = next((p.node_port for p in (svc.spec.ports or []) if p.node_port), None)
                        if np:
                            node_ports[svc.metadata.name] = np
            except Exception:
                pass
            for dep in deps:
                name = dep.metadata.name
                containers = dep.spec.template.spec.containers or []
                image = containers[0].image if containers else None
                # Pods whose name starts with the deployment name (rough but adequate).
                dep_pods = [
                    {"name": n, "phase": p.phase, "restarts": p.restarts}
                    for n, p in pods.items()
                    if n.startswith(name)
                ]
                # Contract metadata (kind + configurable) so the UI can show where a
                # surface is served (api / ui / internal) and only offer Configure to
                # services that actually expose a /contract. Cached by (name, image).
                ready = (dep.status.ready_replicas or 0) >= 1
                meta = _CONTRACT_META.get((name, image))
                if meta is None:
                    c = self.service_contract(name)
                    if c.get("available"):
                        contract = c.get("contract") or {}
                        meta = {
                            "kind": contract.get("kind"),
                            "configurable": True,
                            # Default subdomain the service declares for itself (contract
                            # field `subdomain`); null until the upstream contracts add it.
                            # The UI derives <subdomain>.<base> and infers a default when null.
                            "subdomain": contract.get("subdomain"),
                        }
                        _CONTRACT_META[(name, image)] = meta
                    else:
                        meta = {"kind": None, "configurable": False, "subdomain": None}
                        # Cache the "no contract" result only once the pod is Ready:
                        # a ready pod that still has no /contract genuinely has none,
                        # so stop re-probing it every poll (avoids a slow inventory).
                        # A not-yet-Ready pod is left uncached so the next poll retries
                        # until it serves its contract (just-deployed adapters).
                        if ready:
                            _CONTRACT_META[(name, image)] = meta
                services.append({
                    "name": name,
                    "namespace": ns,
                    "image": image,
                    "replicas": dep.spec.replicas or 0,
                    "ready_replicas": dep.status.ready_replicas or 0,
                    "managed": name in MANAGED_DEPLOYMENTS,
                    "labels": dep.metadata.labels or {},
                    "node_port": node_ports.get(name),
                    "kind": meta["kind"],
                    "configurable": meta["configurable"],
                    "subdomain": meta["subdomain"],
                    "pods": dep_pods,
                })
        return {"services": services}

    # ── Adapter registry (engine = authority; adapters self-register) ──────────
    def list_adapters(self) -> list[dict[str, Any]]:
        """The live registry from the engine (GET /adapters via the API-server
        service proxy). Each entry carries membership + reachability: name, kind,
        base_url, registered_via (self|seed|manual), last_seen_s_ago, fail_count /
        in_cooldown, and a derived state (live|unreachable|stale). Degrades to an
        empty list when the engine is briefly unreachable (e.g. mid-rollout)."""
        try:
            raw = self.k8s.service_proxy_get(POSITIONING_NS, ENGINE_SERVICE, ENGINE_PORT, "adapters")
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
            return data.get("adapters", [])
        except Exception:
            return []

    # ── Asset Identity Map (the gateway is the authority: GET/PUT /assets) ─────
    # /assets enforces a CAMARA JWT, so unlike the engine reads we cannot use the
    # API-server service proxy (its Authorization slot authenticates to the API
    # server). We reach the gateway NodePort and FORWARD the caller's Bearer; the
    # asset routes are admin-only and a dashboard-admin token is composite with
    # camara-location-read, which the gateway requires. An org-less admin token is
    # the operator bypass, so the editor sees every org's assets.
    def _gateway_base_url(self) -> str:
        port = self.k8s.service_nodeport(CAMARA_NS, GATEWAY_SERVICE, GATEWAY_PORT)
        return f"http://{self.k8s.any_node_ip()}:{port}"

    @staticmethod
    def _bearer(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _gateway_get(self, token: str, path: str) -> dict[str, Any]:
        try:
            resp = httpx.get(f"{self._gateway_base_url()}{path}", headers=self._bearer(token), timeout=6.0)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GatewayError(exc.response.status_code, exc.response.text)
        return resp.json()

    def list_assets(self, token: str) -> dict[str, Any]:
        """The full Asset Identity Map from the gateway (GET /assets)."""
        return self._gateway_get(token, "/assets")

    def asset_details(self, token: str, asset_id: str) -> dict[str, Any]:
        """Per-asset detail (position/telemetry) for the UI (GET /assets/{id}/details)."""
        return self._gateway_get(token, f"/assets/{asset_id}/details")

    def put_assets(self, token: str, body: dict[str, Any]) -> dict[str, Any]:
        """Replace the Asset Identity Map (PUT /assets). The dashboard sends the full
        set (load-all, edit, save-all); the gateway validates against asset.schema.json."""
        try:
            resp = httpx.put(f"{self._gateway_base_url()}/assets", headers=self._bearer(token), json=body, timeout=8.0)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GatewayError(exc.response.status_code, exc.response.text)
        return {"status": "applied", "count": len((body or {}).get("assets", []))}

    def unregister_adapter(self, name: str) -> dict[str, Any]:
        """Force-remove an adapter from the engine registry (DELETE /adapters/{name}).
        Self-registered adapters normally deregister on shutdown or TTL-evict; this
        is for clearing a stale entry, and is also called when a deployed adapter
        workload is deleted. Idempotent: a 404 is treated as already-absent."""
        _validate_name(name)
        try:
            self.k8s.service_proxy_delete(POSITIONING_NS, ENGINE_SERVICE, ENGINE_PORT, f"adapters/{name}")
            return {"status": "unregistered", "name": name}
        except Exception:
            return {"status": "absent", "name": name}

    def upgrade_adapter(self, name: str, image: str) -> dict[str, Any]:
        """Bring a catalog adapter to a current image, in place. Patches ONLY the
        deployment image (envFrom config, volumes, probes all preserved) and merges
        the self-registration env into its <name>-config (an adapter from before
        v0.6.0 has none, so the upgrade also makes it self-register). The new pod
        re-reads the merged config via the existing envFrom and announces itself."""
        _validate_name(name)
        _validate_image(image)
        # Port from the live deployment so ADAPTER_BASE_URL is right (fallback 8080).
        port = 8080
        try:
            dep = self.k8s.apps.read_namespaced_deployment(name=name, namespace=POSITIONING_NS)
            cps = (dep.spec.template.spec.containers[0].ports or [])
            if cps and cps[0].container_port:
                port = cps[0].container_port
        except Exception:
            pass
        cm_name = f"{name}-config"
        try:
            data = dict((self.k8s.get_configmap(POSITIONING_NS, cm_name).get("data") or {}))
        except Exception:
            data = {}
        data.setdefault("POSITIONING_ENGINE_URL", f"http://{ENGINE_SERVICE}.{POSITIONING_NS}.svc.cluster.local:{ENGINE_PORT}")
        data.setdefault("ADAPTER_NAME", name)
        data.setdefault("ADAPTER_BASE_URL", f"http://{name}.{POSITIONING_NS}.svc.cluster.local:{port}")
        self.k8s.apply_configmap(POSITIONING_NS, cm_name, data)
        # Patch the image AND ensure envFrom binds the config/secret (an adapter
        # deployed before v0.6.0 may bind neither, so the merged self-reg env would
        # never reach the pod). Inline env and volumes are left untouched.
        self.k8s.set_workload_image(POSITIONING_NS, name, image, envfrom=[cm_name, f"{name}-secrets"])
        return {"status": "upgrading", "name": name, "image": image}

    # ── Fusion config ─────────────────────────────────────────────────────────
    def set_fusion(self, payload: FusionConfigPayload) -> dict[str, Any]:
        cm = self.k8s.get_configmap(POSITIONING_NS, ENGINE_CONFIGMAP)
        data = dict(cm.get("data") or {})
        if payload.strategy is not None:
            data["FUSION_STRATEGY"] = payload.strategy
        if payload.compare is not None:
            data["FUSION_COMPARE"] = payload.compare
        if payload.device_map is not None:
            data["DEVICE_MAP"] = payload.device_map
        self.k8s.apply_configmap(POSITIONING_NS, ENGINE_CONFIGMAP, data)
        self.k8s.restart_deployment(POSITIONING_NS, ENGINE_DEPLOYMENT)
        return {"status": "applied", "engine_restarted": True}

    # ── Deploy-from-image ─────────────────────────────────────────────────────
    def _apply_workload(self, ns: str, name: str, image: str, port: int, env, image_pull_secret) -> None:
        """Create-or-update a Deployment + ClusterIP Service from a plain image.
        Shared by adapter deploy (positioning) and generic workload deploy.

        Uses the SAME single config mechanism as the managed services and the
        Configure wizard (apply_service_config): plain vars in a `<name>-config`
        ConfigMap, sensitive vars in a `<name>-secrets` Secret, both consumed via
        envFrom with optional: true. This is what makes a deployed adapter
        configurable afterwards: the wizard patches the very same objects and the
        pod re-reads them on rollout. (Inline container env would be invisible to
        the wizard, so a later Configure would write objects nothing consumes.)"""
        cm_name, secret_name = f"{name}-config", f"{name}-secrets"
        plain = {e.name: e.value for e in env if not e.sensitive}
        sensitive = {e.name: e.value for e in env if e.sensitive}

        # Seed the config objects the deployment binds via envFrom. The ConfigMap
        # is created even when empty so the wizard has a stable object to patch.
        self.k8s.apply_configmap(ns, cm_name, plain)
        if sensitive:
            self.k8s.upsert_secret(ns, secret_name, sensitive)

        container: dict[str, Any] = {
            "name": name,
            "image": image,
            "imagePullPolicy": "IfNotPresent",
            "ports": [{"containerPort": port, "name": "http"}],
            "envFrom": [
                {"configMapRef": {"name": cm_name, "optional": True}},
                {"secretRef": {"name": secret_name, "optional": True}},
            ],
            "resources": {
                "requests": {"cpu": "50m", "memory": "64Mi"},
                "limits": {"cpu": "500m", "memory": "256Mi"},
            },
            "readinessProbe": {
                "httpGet": {"path": "/health", "port": port},
                "initialDelaySeconds": 5,
                "periodSeconds": 5,
                "failureThreshold": 6,
            },
        }

        pod_spec: dict[str, Any] = {
            "nodeSelector": {"kubernetes.io/hostname": "worker"},
            "containers": [container],
        }
        if image_pull_secret:
            pod_spec["imagePullSecrets"] = [{"name": image_pull_secret}]

        labels = {"app": name, "app.kubernetes.io/managed-by": "dashboard-northbound"}
        self.k8s.upsert_deployment(ns, {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": name, "namespace": ns, "labels": labels},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": name}},
                "template": {"metadata": {"labels": labels}, "spec": pod_spec},
            },
        })
        self.k8s.upsert_service(ns, {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": name, "namespace": ns, "labels": labels},
            "spec": {
                "selector": {"app": name},
                "ports": [{"port": port, "targetPort": port, "name": "http"}],
                "type": "ClusterIP",
            },
        })

    def deploy_image(self, req: DeployImageRequest) -> dict[str, Any]:
        # Adapter deploy into the positioning namespace. v0.6.0: the adapter
        # SELF-REGISTERS with the engine, so we inject the registration env (the
        # image cannot know its own Service URL or the operator-chosen name); it
        # then announces itself + heartbeats and the engine evicts it on TTL. No
        # manual ADAPTER_URLS step. Operator-supplied env wins (not overridden).
        _validate_name(req.name)
        _validate_image(req.image)
        engine_url = f"http://{ENGINE_SERVICE}.{POSITIONING_NS}.svc.cluster.local:{ENGINE_PORT}"
        base_url = f"http://{req.name}.{POSITIONING_NS}.svc.cluster.local:{req.port}"
        self_reg = [
            DeployEnvVar(name="POSITIONING_ENGINE_URL", value=engine_url),
            DeployEnvVar(name="ADAPTER_NAME", value=req.name),
            DeployEnvVar(name="ADAPTER_BASE_URL", value=base_url),
        ]
        # Only override ADAPTER_KIND when the operator chose one; otherwise let the
        # adapter image keep its own default (e.g. wifi-positioning -> "wifi").
        if req.kind:
            self_reg.append(DeployEnvVar(name="ADAPTER_KIND", value=req.kind))
        have = {e.name for e in req.env}
        env = list(req.env) + [e for e in self_reg if e.name not in have]
        self._apply_workload(POSITIONING_NS, req.name, req.image, req.port, env, req.image_pull_secret)
        return {"status": "deployed", "name": req.name, "namespace": POSITIONING_NS, "self_registers": True}

    def deploy_workload(self, req) -> dict[str, Any]:
        # Generic workload deploy into an allow-listed namespace; no adapter registration.
        _validate_name(req.name)
        _validate_image(req.image)
        if req.namespace not in NORTHBOUND_NAMESPACES:
            raise ValueError(f"namespace must be one of {NORTHBOUND_NAMESPACES}")
        self._apply_workload(req.namespace, req.name, req.image, req.port, req.env, req.image_pull_secret)
        return {"status": "deployed", "name": req.name, "namespace": req.namespace}

    def delete_adapter_workload(self, name: str) -> dict[str, Any]:
        _validate_name(name)
        self.unregister_adapter(name)
        self.k8s.delete_service(POSITIONING_NS, name)
        self.k8s.delete_deployment(POSITIONING_NS, name)
        # Config objects the deploy/wizard bind via envFrom, the vendor schema
        # ConfigMap, plus the legacy `<name>-env` secret (all no-op if absent).
        self.k8s.delete_configmap(POSITIONING_NS, f"{name}-config")
        self.k8s.delete_configmap(POSITIONING_NS, f"{name}-files")
        self.k8s.delete_secret(POSITIONING_NS, f"{name}-secrets")
        self.k8s.delete_secret(POSITIONING_NS, f"{name}-env")
        return {"status": "deleted", "name": name}

    # ── Managed image rollout ──────────────────────────────────────────────────
    def set_managed_image(self, deployment: str, image: str) -> dict[str, Any]:
        if deployment not in MANAGED_DEPLOYMENTS:
            raise ValueError(f"Unknown managed deployment '{deployment}' (one of {sorted(MANAGED_DEPLOYMENTS)})")
        _validate_image(image)
        ns = MANAGED_DEPLOYMENTS[deployment]
        # Strategic-merge patch: containers merge by name (== deployment name).
        patch = {"spec": {"template": {"spec": {"containers": [{"name": deployment, "image": image}]}}}}
        self.k8s.apps.patch_namespaced_deployment(name=deployment, namespace=ns, body=patch)
        return {"status": "rolled-out", "deployment": deployment, "namespace": ns, "image": image}

    # ── Contract guidance (static) ─────────────────────────────────────────────
    def contract(self) -> dict[str, Any]:
        return {
            "measurement_schema": MEASUREMENT_SCHEMA,
            "endpoints": ["GET /measurement/{device_id}", "GET /health"],
            "python_skeleton": ADAPTER_SKELETON,
            "env_contract_template": (
                "service: my-adapter\n"
                "description: One-line purpose.\n"
                "required:\n"
                "  - name: SOME_URL\n"
                "    description: Upstream the adapter talks to.\n"
                "    sensitive: false\n"
                "    example: http://host:8080\n"
                "optional:\n"
                "  - name: API_KEY\n"
                "    description: Vendor credential.\n"
                "    sensitive: true\n"
                "    default: \"\"\n"
            ),
            "docs": {
                "adapter_contract": "https://github.com/Jacobbista/5g-northbound/blob/main/docs/adapters.md",
                "rest_adapter": "https://github.com/Jacobbista/5g-northbound/blob/main/docs/integrating-a-vendor-rest-api.md",
                "env_contract": "https://github.com/Jacobbista/5g-northbound/blob/main/docs/deployment.md",
            },
        }

    # ── Live per-service contract (served by each service's /contract) ──────────
    def service_contract(self, name: str) -> dict[str, Any]:
        """Fetch a service's own /contract through the API-server service proxy.

        The dashboard backend runs OUTSIDE the cluster (ansible VM), so it cannot
        resolve *.svc.cluster.local. The proxy subresource reaches ClusterIP and
        NodePort services alike via the kube API. /contract is metadata (kind,
        external_origin var, required/recommended/optional env) served by a
        degraded-bootable, auth-exempt endpoint; it carries no config VALUES.
        Returns {available: False, ...} when a service has no /contract yet, so
        the wizard degrades gracefully instead of erroring.
        """
        _validate_name(name)
        svc_obj = None
        ns = None
        for cand in NORTHBOUND_NAMESPACES:
            try:
                for s in self.k8s.core.list_namespaced_service(namespace=cand).items:
                    if s.metadata.name == name:
                        svc_obj, ns = s, cand
                        break
            except Exception:
                continue
            if ns:
                break
        if ns is None:
            return {"available": False, "service": name, "error": "service not found"}
        # The service-proxy needs the port spelled out: the portless form defaults
        # to :80 and reports "no endpoints" when the service listens elsewhere.
        port = next((p.port for p in (svc_obj.spec.ports or [])), None)
        proxy_name = f"{name}:{port}" if port else name
        try:
            # _preload_content=False returns the raw HTTP response. WITHOUT it the
            # client coerces an application/json body into a single-quoted Python
            # dict repr (invalid JSON), so json.loads fails. Read the raw bytes.
            resp = self.k8s.core.connect_get_namespaced_service_proxy_with_path(
                name=proxy_name, namespace=ns, path="contract", _preload_content=False,
            )
            body = resp.data
            if isinstance(body, (bytes, bytearray)):
                body = body.decode("utf-8")
            data = json.loads(body)
            return {"available": True, "service": name, "namespace": ns, "contract": data}
        except Exception as e:  # 404 (no endpoint yet), unreachable, parse error
            return {"available": False, "service": name, "namespace": ns, "error": str(e)[:200]}

    def service_config(self, name: str) -> dict[str, Any]:
        """Contract schema + current values, for the guided setup.

        Discovers the envFrom ConfigMap/Secret from the deployment (single input:
        pod env vars, routed by the contract's `sensitive` flag). Reports current
        NON-sensitive values from the ConfigMap; sensitive vars are reported only
        as set/unset (never their value).
        """
        c = self.service_contract(name)
        if not c.get("available"):
            return {"available": False, "service": name, "error": c.get("error", "no contract")}
        ns = c["namespace"]
        contract = c["contract"]
        cm_name = secret_name = None
        dep = None
        try:
            dep = self.k8s.apps.read_namespaced_deployment(name=name, namespace=ns)
            for ef in (dep.spec.template.spec.containers[0].env_from or []):
                if ef.config_map_ref:
                    cm_name = ef.config_map_ref.name
                if ef.secret_ref:
                    secret_name = ef.secret_ref.name
        except Exception:
            pass

        def file_state(path: str) -> str:
            """How a *_FILE path is provided:
              managed   - mounted from our <name>-files ConfigMap (dashboard owns it)
              external  - mounted from a PVC / other volume (the service owns it)
              ephemeral - not mounted, but a file is present at runtime (loaded via
                          the service API into the container fs; lost on restart)
              absent    - nothing there at all (dashboard can provide the document)
            """
            if not path or dep is None:
                return "absent"
            spec = dep.spec.template.spec
            vol_kind = {}
            for v in (spec.volumes or []):
                if getattr(v, "persistent_volume_claim", None):
                    vol_kind[v.name] = "external"
                elif getattr(v, "config_map", None):
                    vol_kind[v.name] = "managed" if v.config_map.name == f"{name}-files" else "external"
                else:
                    vol_kind[v.name] = "external"
            for cont in (spec.containers or []):
                for vm in (cont.volume_mounts or []):
                    mp = vm.mount_path or ""
                    if path == mp or path.startswith(mp.rstrip("/") + "/"):
                        return vol_kind.get(vm.name, "external")
            # Not mounted anywhere: is a copy present in the pod (runtime-loaded)?
            return "ephemeral" if self._read_pod_file(name, ns, path) else "absent"
        cm_data: dict[str, str] = {}
        if cm_name:
            try:
                cm_data = self.k8s.get_configmap(ns, cm_name).get("data") or {}
            except Exception:
                pass
        secret_keys: set[str] = set()
        if secret_name:
            try:
                sec = self.k8s.core.read_namespaced_secret(name=secret_name, namespace=ns)
                secret_keys = set((sec.data or {}).keys())
            except Exception:
                pass

        def annotate(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
            out = []
            for e in entries or []:
                n = e.get("name")
                if e.get("sensitive"):
                    item = {**e, "set": n in secret_keys}  # never expose the value
                else:
                    item = {**e, "value": cm_data.get(n), "set": n in cm_data}
                # Annotate file fields (path-valued *_FILE / *_PATH) so the UI knows
                # whether the dashboard owns the document (editor + readiness) or the
                # service does (PVC -> hands off).
                fpath = e.get("value") or cm_data.get(n) or e.get("default") or ""
                if _is_file_field(n, fpath):
                    item["file_state"] = file_state(fpath)
                    item["file_path"] = fpath
                out.append(item)
            return out

        env = contract.get("env", {})
        return {
            "available": True,
            "service": name,
            "namespace": ns,
            "kind": contract.get("kind"),
            "external_origin": contract.get("external_origin"),
            "description": contract.get("description"),
            "config_map": cm_name,
            "secret": secret_name,
            "env": {grp: annotate(env.get(grp)) for grp in ("required", "recommended", "optional")},
        }

    def apply_service_config(self, name: str, values: dict[str, str | None]) -> dict[str, Any]:
        """Single-mechanism apply: route each var by the contract's `sensitive`
        flag to a Secret (sensitive) or ConfigMap (not), both consumed via the
        deployment's envFrom, then rollout so the pod re-reads them (and, for
        frontends, the image entrypoint re-renders env-config.js).

        Both writes are strategic-merge patches, so untouched keys are preserved.
        The deployment must declare envFrom for the ConfigMap/Secret (the manifests
        do, with optional: true so a degraded pod still boots); this manages the
        content, not the wiring.
        """
        c = self.service_contract(name)
        if not c.get("available"):
            raise ValueError(f"{name} exposes no /contract; refusing to map config blindly")
        ns = c["namespace"]
        contract = c["contract"]
        sensitive: dict[str, bool] = {}
        for grp in ("required", "recommended", "optional"):
            for e in (contract.get("env", {}).get(grp) or []):
                sensitive[e["name"]] = bool(e.get("sensitive"))
        unknown = [k for k in values if k not in sensitive]
        if unknown:
            raise ValueError(f"vars not in {name} contract: {sorted(unknown)}")
        # Discover the envFrom ConfigMap/Secret names; fall back to a convention.
        cm_name, secret_name = f"{name}-config", f"{name}-secrets"
        try:
            dep = self.k8s.apps.read_namespaced_deployment(name=name, namespace=ns)
            for ef in (dep.spec.template.spec.containers[0].env_from or []):
                if ef.config_map_ref:
                    cm_name = ef.config_map_ref.name
                if ef.secret_ref:
                    secret_name = ef.secret_ref.name
        except Exception:
            pass
        # A null value UNSETS the var (delete the key); else set it. Routed by the
        # contract's `sensitive` flag to the Secret or the ConfigMap.
        set_vals = {k: v for k, v in values.items() if v is not None}
        unset_keys = [k for k, v in values.items() if v is None]
        cm_vars = {k: str(v) for k, v in set_vals.items() if not sensitive.get(k)}
        secret_vars = {k: str(v) for k, v in set_vals.items() if sensitive.get(k)}
        cm_unset = [k for k in unset_keys if not sensitive.get(k)]
        secret_unset = [k for k in unset_keys if sensitive.get(k)]
        if cm_vars:
            self.k8s.apply_configmap(ns, cm_name, cm_vars)
        if secret_vars:
            self.k8s.upsert_secret(ns, secret_name, secret_vars)
        if cm_unset:
            self.k8s.unset_configmap_keys(ns, cm_name, cm_unset)
        if secret_unset:
            self.k8s.unset_secret_keys(ns, secret_name, secret_unset)
        self.k8s.restart_deployment(ns, name)
        return {
            "status": "applied", "service": name, "namespace": ns,
            "config_map": cm_name if cm_vars else None,
            "secret": secret_name if secret_vars else None,
            "applied": sorted(values.keys()), "restarted": True,
        }

    def adapter_bindings(self) -> dict[str, Any]:
        """Report, per known consumer/field, the current adapter binding and the
        deployed adapters of the matching kind. Lets the UI show the association
        at a glance, auto-bind the unambiguous single-adapter case, and offer a
        switcher when more than one adapter of a kind is deployed. The consumer
        field is single-valued (e.g. placement-editor's REST_ADAPTER_URL points at
        ONE rest-adapter), so >1 candidate is a choice, not an auto-bind."""
        services = self.inventory().get("services", [])
        out: list[dict[str, Any]] = []
        for consumer, fields in _ADAPTER_BINDINGS.items():
            if not any(s["name"] == consumer for s in services):
                continue
            cfg = self.service_config(consumer)
            current: dict[str, Any] = {}
            if cfg.get("available"):
                for grp in ("required", "recommended", "optional"):
                    for e in (cfg.get("env", {}).get(grp) or []):
                        current[e.get("name")] = e.get("value")
            for b in fields:
                cands = [
                    {"name": s["name"], "url": f"http://{s['name']}.{s['namespace']}.svc.cluster.local:8080"}
                    for s in services if _image_basename(s.get("image")) == b["kind"]
                ]
                cur = current.get(b["field"])
                bound_to = next((c["name"] for c in cands if c["url"] == cur), None)
                out.append({
                    "consumer": consumer,
                    "field": b["field"],
                    "kind": b["kind"],
                    "current": cur,
                    "candidates": cands,
                    "bound_to": bound_to,
                    # Exactly one candidate and not already bound to it -> unambiguous,
                    # safe to auto-bind. >1 -> the UI must let the operator choose.
                    "auto": len(cands) == 1 and bound_to is None,
                })
        return {"bindings": out}

    # ── File-backed config (generic, declarative, reproducible) ────────────────
    # A contract var named like a path (`*_FILE`, e.g. the rest-adapter's
    # SCHEMA_FILE) means the service reads a DOCUMENT from that path, not a scalar.
    # Rather than an ephemeral in-pod write, the dashboard
    # stores every such document in ONE `<name>-files` ConfigMap and mounts each at
    # its declared path (subPath). It is then declarative k8s state, re-applied on
    # every deploy, no manual step. Driven purely by what the contract exposes, so
    # it works for any service/field without special-casing.
    def _service_namespace(self, name: str) -> str:
        for ns in NORTHBOUND_NAMESPACES:
            try:
                if any(d.metadata.name == name for d in self.k8s.apps.list_namespaced_deployment(namespace=ns).items):
                    return ns
            except Exception:
                continue
        return POSITIONING_NS

    def _read_pod_file(self, name: str, ns: str, path: str) -> str | None:
        """Read a file at `path` from the service's running pod (best-effort), to
        detect a document loaded at runtime but not declaratively mounted (i.e.
        ephemeral, lost on restart). Generic: any service/path, no special API."""
        if not re.match(r"^/[\w./-]+$", path or ""):
            return None
        try:
            pods = self.k8s.core.list_namespaced_pod(namespace=ns, label_selector=f"app={name}").items
            pod = next((p.metadata.name for p in pods if (p.status.phase == "Running")), None)
            if not pod:
                return None
            out = self.k8s.exec_in_pod(ns, pod, ["sh", "-c", f"cat {path} 2>/dev/null"], container=name)
            return out if (out and out.strip()) else None
        except Exception:
            return None

    def get_service_file(self, name: str, path: str) -> dict[str, Any]:
        """Current content of the document at `path`: the dashboard-managed copy
        (the <name>-files ConfigMap) if present, else the runtime copy read from the
        pod (ephemeral=True), so the editor can pre-fill it for one-click persist."""
        _validate_name(name)
        ns = self._service_namespace(name)
        key = (path or "").rsplit("/", 1)[-1] or "file"
        try:
            cm = self.k8s.get_configmap(ns, f"{name}-files")
            content = (cm.get("data") or {}).get(key)
            if content is not None:
                return {"name": name, "path": path, "content": content, "ephemeral": False}
        except Exception:
            pass
        runtime = self._read_pod_file(name, ns, path)
        return {"name": name, "path": path, "content": runtime, "ephemeral": runtime is not None}

    def apply_service_file(self, name: str, path: str, content: str) -> dict[str, Any]:
        """Store `content` for the document at `path` in the `<name>-files`
        ConfigMap and mount it there (subPath). Idempotent strategic-merge, so
        multiple file-fields accumulate in one ConfigMap / volume."""
        _validate_name(name)
        if not path or not path.startswith("/"):
            raise ValueError("path must be an absolute container path")
        if path.endswith(".json"):
            try:
                json.loads(content)
            except Exception as e:
                raise ValueError(f"content is not valid JSON: {e}")
        ns = self._service_namespace(name)
        key = path.rsplit("/", 1)[-1] or "file"
        cm_name = f"{name}-files"
        self.k8s.apply_configmap(ns, cm_name, {key: content})
        # One "service-files" volume; one mount per document (merge key = mountPath).
        patch = {"spec": {"template": {"spec": {
            "volumes": [{"name": "service-files", "configMap": {"name": cm_name}}],
            "containers": [{
                "name": name,
                "volumeMounts": [{"name": "service-files", "mountPath": path, "subPath": key, "readOnly": True}],
            }],
        }}}}
        self.k8s.apps.patch_namespaced_deployment(name=name, namespace=ns, body=patch)
        self.k8s.restart_deployment(ns, name)
        return {"status": "applied", "name": name, "config_map": cm_name, "mount": path}

    def service_readiness(self) -> dict[str, Any]:
        """Per configurable service, what is still missing for it to function: any
        required env var unset, plus any *_FILE document field with nothing mounted
        at its path. Lets the UI flag a box that still needs configuration (e.g. a
        rest-adapter with no vendor schema). A *_FILE counts as satisfied when ANY
        volume is mounted there (our schema ConfigMap, or a PVC the service writes
        itself), so PVC-managed files don't flag."""
        out: dict[str, Any] = {}
        for s in self.inventory().get("services", []):
            name = s["name"]
            if not s.get("configurable"):
                continue
            cfg = self.service_config(name)
            if not cfg.get("available"):
                continue
            env = cfg.get("env", {})
            missing: list[str] = []
            ephemeral: list[str] = []
            for e in (env.get("required") or []):
                if not e.get("set") and "file_state" not in e:
                    missing.append(e["name"])
            # File fields (file_state set by service_config): "absent" -> truly
            # missing (needs config); "ephemeral" -> loaded at runtime but not
            # persisted (a warning, works now but lost on restart -> persist it);
            # "external"/"managed" are fine.
            for grp in ("required", "recommended", "optional"):
                for e in (env.get(grp) or []):
                    if "file_state" not in e:
                        continue
                    if e.get("file_state") == "absent":
                        missing.append(e["name"])
                    elif e.get("file_state") == "ephemeral":
                        ephemeral.append(e["name"])
            out[name] = {"needs_config": len(missing) > 0, "missing": missing, "ephemeral": ephemeral}
        return {"readiness": out}

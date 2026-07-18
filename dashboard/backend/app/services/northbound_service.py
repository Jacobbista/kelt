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
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from app.models import DeployEnvVar, DeployImageRequest, FusionConfigPayload
from app.services.k8s_service import K8sService
from app.services.nf_service import ANSIBLE_CFG, ANSIBLE_DIR, ANSIBLE_PLAYBOOK_BIN

# Operator-persisted config sourced into the ansible env for an update-all run, so
# re-running phase 10 keeps every other surface's flags. Mirrors apps_service.
TESTBED_ENV = Path("/vagrant/.testbed.env")
TESTBED_SECRETS = Path("/vagrant/.testbed.secrets")
PHASE10_PLAYBOOK = f"{ANSIBLE_DIR}/phases/10-northbound/playbook.yml"

# Companion image versions. The filtered CI rebuilds only the images that changed, so
# each 5g-northbound image advances INDEPENDENTLY (e.g. wifi at 0.8.9 while the engine
# is still 0.8.8) — there is no single shared release tag. Each phase-10-managed image
# reads its OWN release-tag env var (below) with the baked pin as the fresh-clone
# fallback; Update all resolves each image's latest tag on ghcr and persists the ones
# it moved to .testbed.versions, so a phase re-run keeps them instead of downgrading.
# This file is dashboard-owned and separate from .testbed.env because testbed-config's
# save_config rewrites .testbed.env wholesale. testbed-config sources it for `run-phase`.
TESTBED_VERSIONS = Path("/vagrant/.testbed.versions")
COMPANION_PREFIX = "ghcr.io/jacobbista/5g-northbound/"
# Phase-10-managed image basename -> the env var its role default reads (lookup env).
# The rest (wifi-positioning, rest-adapter) are catalog adapters rolled via kubectl set,
# so they need no env var.
COMPANION_TAG_VARS = {
    "positioning-engine": "POSITIONING_ENGINE_TAG",
    "mock-positioning": "MOCK_POSITIONING_TAG",
    "camara-gateway": "CAMARA_GATEWAY_TAG",
    "placement-editor": "PLACEMENT_EDITOR_TAG",
    "positioning-demo": "POSITIONING_DEMO_TAG",
}
PHASE_MANAGED_BASENAMES = set(COMPANION_TAG_VARS)
CATALOG_BASENAMES = {"wifi-positioning", "rest-adapter"}
# Per-repo latest-tag cache (repo path -> (ts, tag|None)); the badge polls versions().
_GHCR_CACHE: dict[str, tuple[float, str | None]] = {}
_GHCR_TTL = 300.0


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
    """ghcr.io/jacobbista/5g-northbound/rest-adapter:0.8.6 -> rest-adapter."""
    return (image or "").rsplit("/", 1)[-1].split("@")[0].split(":")[0]


def _adapter_probes(port: int) -> dict[str, Any]:
    """Readiness on /ready (config-aware: 503 + reason while degraded, e.g. a
    rest-adapter with no schema, so the dashboard shows it NOT ready instead of falsely
    green); liveness on /health (process up). Every 5g-northbound adapter and the SDK
    skeleton expose both. Applied on deploy AND upgrade, so a rolled-forward adapter
    also gets the honest probe. See docs/architecture/positioning-adapters.md."""
    return {
        "readinessProbe": {"httpGet": {"path": "/ready", "port": port},
                           "initialDelaySeconds": 5, "periodSeconds": 5, "failureThreshold": 6},
        "livenessProbe": {"httpGet": {"path": "/health", "port": port},
                          "initialDelaySeconds": 10, "periodSeconds": 10, "failureThreshold": 6},
    }


def _image_tag(image: str | None) -> str:
    """...rest-adapter:0.8.6 -> 0.8.6 (empty when digest-pinned or untagged)."""
    tail = (image or "").rsplit("/", 1)[-1]
    return tail.split(":", 1)[1] if ":" in tail else ""


def _semver_key(tag: str) -> tuple[int, int, int] | None:
    """Numeric (major, minor, patch) for ordering; None for non-semver tags (latest,
    sha-*), which are excluded from release comparison."""
    parts = (tag or "").split(".")
    if len(parts) < 2:
        return None
    try:
        nums = [int(p) for p in parts[:3]]
    except ValueError:
        return None
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def _repo_path(image: str | None) -> str:
    """ghcr.io/jacobbista/5g-northbound/x:0.8.6 -> jacobbista/5g-northbound/x."""
    body = (image or "").split("ghcr.io/", 1)[-1]
    return body.rsplit(":", 1)[0].split("@")[0]


def _ghcr_latest_in_major(repo_path: str, major: int) -> str | None:
    """Highest semver tag on ghcr for repo_path within the given major (cap: a major
    bump is a KELT release, not a live roll). Anonymous pull token. Best-effort:
    any failure returns None so a registry blip shows no phantom update."""
    try:
        with httpx.Client(timeout=6.0) as c:
            tok = c.get("https://ghcr.io/token",
                        params={"scope": f"repository:{repo_path}:pull",
                                "service": "ghcr.io"}).json().get("token")
            if not tok:
                return None
            tags = c.get(f"https://ghcr.io/v2/{repo_path}/tags/list",
                         headers={"Authorization": f"Bearer {tok}"}).json().get("tags") or []
    except Exception:
        return None
    best_key: tuple[int, int, int] | None = None
    best_tag: str | None = None
    for t in tags:
        k = _semver_key(t)
        if k and k[0] == major and (best_key is None or k > best_key):
            best_key, best_tag = k, t
    return best_tag


# Adapters whose service WRITES a document at runtime: it must be PVC-backed or the
# write is lost on restart/upgrade. Keyed by image basename -> {env, path}: `env` is
# the env var by which the service is told where to read/write that document, `path`
# is the image default (only its basename is used). The store redirects `env` to a
# file inside STORE_DIR (see _ensure_writable_store) rather than mounting over the
# default path, so no subPath is needed. wifi-positioning persists its calibration
# (tx_power/path_loss_n) into WIFI_CONFIG_PATH. SCHEMA_FILE is writable upstream but
# operator-authored here (no runtime writer), so it stays a ConfigMap.
# See docs/architecture/positioning-adapters.md and
# docs/known-issues/wifi-calibration-subpath-directory.md.
_STATEFUL_DOCS = {
    "wifi-positioning": {"env": "WIFI_CONFIG_PATH", "path": "/app/config/wifi-config.json"},
}
# Dedicated directory the writable PVC is mounted at (whole-dir mount, no subPath).
STORE_DIR = "/data"


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
                labels = dep.metadata.labels or {}
                # Edge apps (phase 12) share the `mec` namespace with northbound's
                # positioning-demo; exclude them so this console lists only
                # positioning/CAMARA services. See docs/architecture/edge-apps.md.
                if labels.get("app.kubernetes.io/managed-by") == "dashboard-apps":
                    continue
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
                # Stateful adapters (wifi-positioning) write a doc at runtime that must be
                # PVC-backed. `persistent` = the b2 store is attached (PVC mounted at
                # STORE_DIR), so the UI can flag an ephemeral calibration and offer a
                # one-click enable. An old subPath store reads as not-persistent → it gets
                # migrated on enable. See _STATEFUL_DOCS / _has_writable_store.
                stateful = _image_basename(image) in _STATEFUL_DOCS
                persistent = (
                    any((m.name == f"{name}-data" and m.mount_path == STORE_DIR)
                        for m in (dep.spec.template.spec.containers[0].volume_mounts or []))
                    if stateful else None
                )
                services.append({
                    "name": name,
                    "namespace": ns,
                    "image": image,
                    "replicas": dep.spec.replicas or 0,
                    "ready_replicas": dep.status.ready_replicas or 0,
                    "managed": name in MANAGED_DEPLOYMENTS,
                    "labels": labels,
                    "node_port": node_ports.get(name),
                    "kind": meta["kind"],
                    "configurable": meta["configurable"],
                    "subdomain": meta["subdomain"],
                    "stateful": stateful,
                    "persistent": persistent,
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

    def discoverable_assets(self, token: str) -> dict[str, Any]:
        """Devices the engine sees across live adapters that are NOT yet onboarded
        (gateway GET /assets/discoverable). Each candidate carries id, source, origin
        (inventory = vendor registry | observed = seen on air) so the UI can prefill an
        onboarding form; the gateway subtracts already-mapped positioning_ids. Onboarding
        is never automatic: the operator commits an explicit PUT /assets."""
        return self._gateway_get(token, "/assets/discoverable")

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
        self.k8s.set_workload_image(POSITIONING_NS, name, image, envfrom=[cm_name, f"{name}-secrets"],
                                    probes=_adapter_probes(port))
        # Stateful adapters (wifi-positioning writes its calibration) get a PVC-backed
        # store so the writes survive this rollout and future ones. Attached
        # unconditionally: the store must exist for a UI/imported calibration to persist,
        # so it cannot depend on a prior Configure (the <name>-files seed is optional,
        # an empty PVC is fine). Idempotent. See _STATEFUL_DOCS.
        if _image_basename(image) in _STATEFUL_DOCS:
            self._ensure_writable_store(name, POSITIONING_NS)
        return {"status": "upgrading", "name": name, "image": image}

    # ── Update all (re-run phase 10 to the KELT-pinned images) ────────────────
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

    def _ansible_env(self) -> dict[str, str]:
        # Source the operator's persisted config so phase 10 re-renders exactly the
        # surfaces they have enabled (rolling them to the pinned images) and does NOT
        # turn on ones they disabled. No forced enable flag.
        env = {**os.environ, "ANSIBLE_CONFIG": ANSIBLE_CFG}
        env.update(self._source_env_file(TESTBED_ENV))
        env.update(self._source_env_file(TESTBED_SECRETS))
        # Per-image release tags the dashboard rolled forward, so the phase re-renders
        # each image at its current tag, not the baked pin.
        env.update(self._source_env_file(TESTBED_VERSIONS))
        return env

    @staticmethod
    def _write_version_overrides(overrides: dict[str, str]) -> None:
        """Persist per-image release-tag env vars (e.g. POSITIONING_ENGINE_TAG=0.8.8)
        to .testbed.versions so a later phase re-run (dashboard or `testbed run-phase
        10`) keeps them instead of downgrading. Merges: rewrites the given keys, leaves
        any other lines intact."""
        if not overrides:
            return
        existing = TESTBED_VERSIONS.read_text().splitlines() if TESTBED_VERSIONS.exists() else []
        kept = [ln for ln in existing
                if ln.strip() and not any(ln.strip().startswith(f"{k}=") for k in overrides)]
        if not any(ln.strip().startswith("#") for ln in kept):
            kept.insert(0, "# Generated by the dashboard — companion images rolled via Update all")
        for k, v in overrides.items():
            kept.append(f"{k}={v}")
        TESTBED_VERSIONS.write_text("\n".join(kept) + "\n")

    def _ghcr_latest_cached(self, repo: str, major: int) -> str | None:
        """ghcr latest-in-major for a repo, cached _GHCR_TTL so the badge poll does not
        hit ghcr every few seconds. Module-level cache (the service is per-request)."""
        now = time.time()
        hit = _GHCR_CACHE.get(repo)
        if hit and now - hit[0] < _GHCR_TTL:
            return hit[1]
        val = _ghcr_latest_in_major(repo, major)
        _GHCR_CACHE[repo] = (now, val)
        return val

    def _latest_for(self, img: str) -> str | None:
        """The latest tag on ghcr for this image's repo within its deployed major, or
        None (ghcr unreachable / not semver). Per image: the filtered CI advances each
        independently."""
        dk = _semver_key(_image_tag(img))
        return self._ghcr_latest_cached(_repo_path(img), dk[0] if dk else 0)

    def _count_tasks(self, playbook: str, env: dict[str, str]) -> int:
        """Pre-count phase tasks via `--list-tasks` for the progress denominator.
        Counts task lines (carry TAGS:) excluding the per-play header lines. Best-
        effort: 0 disables the percentage (indeterminate bar) rather than failing."""
        try:
            out = subprocess.run(
                [ANSIBLE_PLAYBOOK_BIN, playbook, "--list-tasks"],
                cwd=ANSIBLE_DIR, env=env, capture_output=True, text=True, timeout=60,
            )
            return sum(1 for ln in out.stdout.splitlines()
                       if "TAGS:" in ln and "play #" not in ln)
        except Exception:
            return 0

    def update_all(self, on_event: Any = None) -> dict[str, Any]:
        """Roll every behind companion service to ITS OWN latest tag on ghcr (filtered
        CI advances images independently). Persists the moved phase-managed images'
        tags to .testbed.versions and re-runs phase 10 (only when a phase-managed image
        is behind), then patches the behind catalog adapters (wifi, vendor REST) the
        phase does not own, and reconciles the wifi writable store. PVC-backed state and
        ConfigMap/Secret config are preserved. Streams structured progress events."""
        services = self.inventory().get("services", [])

        def emit(ev: dict[str, Any]) -> None:
            if on_event:
                on_event(ev)

        # Resolve each companion image's own latest and collect what is behind.
        behind: list[tuple[str, str, str]] = []  # (name, basename, target_tag)
        for s in services:
            img = s.get("image") or ""
            if not img.startswith(COMPANION_PREFIX):
                continue
            dk = _semver_key(_image_tag(img))
            latest = self._latest_for(img)
            lk = _semver_key(latest) if latest else None
            if dk and lk and dk < lk:
                behind.append((s.get("name"), _image_basename(img), latest))

        # Phase-managed images: persist their new tags and re-run the phase.
        overrides = {COMPANION_TAG_VARS[b]: t for (_, b, t) in behind if b in COMPANION_TAG_VARS}
        if overrides:
            self._write_version_overrides(overrides)
            env = self._ansible_env()
            total = self._count_tasks(PHASE10_PLAYBOOK, env)
            rolled = ", ".join(f"{b}→{t}" for (_, b, t) in behind if b in COMPANION_TAG_VARS)
            emit({"phase": "start", "done": 0, "total": total, "pct": 0 if total else None,
                  "line": f"phase 10-northbound ({rolled}), {total or '?'} tasks"})
            proc = subprocess.Popen(
                [ANSIBLE_PLAYBOOK_BIN, PHASE10_PLAYBOOK],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, cwd=ANSIBLE_DIR, env=env,
            )
            done = 0
            tail: list[str] = []
            for line in proc.stdout:  # type: ignore[union-attr]
                line = line.rstrip()
                tail.append(line)
                del tail[:-40]
                if line.startswith("TASK ["):
                    done += 1
                    pct = min(99, int(done * 100 / total)) if total else None
                    emit({"phase": "run", "done": done, "total": total, "pct": pct, "line": line})
                elif line.startswith(("PLAY RECAP", "fatal:", "failed:")) or "ERROR" in line:
                    emit({"phase": "run", "line": line})
            proc.wait()
            if proc.returncode != 0:
                raise RuntimeError(
                    f"phase 10-northbound failed (rc={proc.returncode})\n" + "\n".join(tail[-25:])
                )
        else:
            emit({"phase": "start", "pct": None, "line": "no phase-managed updates"})

        # Catalog adapters (not phase-managed): patch each behind one to its own latest
        # (upgrade_adapter attaches the wifi writable store, so calibration survives).
        behind_catalog = {n: t for (n, b, t) in behind if b in CATALOG_BASENAMES}
        for s in services:
            name = s.get("name")
            base = _image_basename(s.get("image") or "")
            if name in behind_catalog:
                emit({"phase": "adapters", "line": f"upgrading {name} → {behind_catalog[name]}"})
                self.upgrade_adapter(name, f"{COMPANION_PREFIX}{base}:{behind_catalog[name]}")
            elif base in _STATEFUL_DOCS and not self._has_writable_store(name):
                # Already current but ensure its writable store (wifi calibration) exists.
                emit({"phase": "adapters", "line": f"enabling persistence for {name}"})
                self._ensure_writable_store(name, POSITIONING_NS)

        emit({"phase": "done", "pct": 100, "line": f"complete — {len(behind)} service(s) updated"})
        return {"status": "updated", "updated": len(behind)}

    # ── Version drift (the "updates available" badge) ─────────────────────────
    def versions(self) -> dict[str, Any]:
        """Per companion service: deployed tag vs ITS OWN latest tag on ghcr (filtered
        CI advances images independently) and whether it is behind. Drives the 'updates
        available' badge. Non-companion images (custom workloads, oauth2-proxy) skipped."""
        services = self.inventory().get("services", [])
        out: list[dict[str, Any]] = []
        behind = 0
        for s in services:
            img = s.get("image") or ""
            if not img.startswith(COMPANION_PREFIX):
                continue
            dep = _image_tag(img)
            latest = self._latest_for(img)
            dk, lk = _semver_key(dep), (_semver_key(latest) if latest else None)
            is_behind = bool(dk and lk and dk < lk)
            behind += 1 if is_behind else 0
            out.append({"name": s.get("name"), "deployed": dep, "latest": latest,
                        "managed": _image_basename(img) in PHASE_MANAGED_BASENAMES,
                        "behind": is_behind})
        return {"services": out, "behind_count": behind}

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
            **_adapter_probes(port),
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
        # Stateful adapters (wifi-positioning writes its calibration at runtime) get a
        # PVC-backed store at deploy time, so a calibration set/imported in the adapter's
        # OWN UI persists across restart/upgrade with no prior dashboard Configure. The
        # service starts with an empty store and creates the file on first write.
        if _image_basename(req.image) in _STATEFUL_DOCS:
            self._ensure_writable_store(req.name, POSITIONING_NS)
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

    def discover_raw(self, name: str) -> dict[str, Any]:
        """Raw vendor device records from an adapter's GET /discover?raw=1, for the
        guided classify builder (operator sees the vendor's native field names to
        author the mapping + classify predicates).

        ADMIN-ONLY at the router: the raw payload is the vendor's own record and can
        carry network secrets (Wittra `state.network.panid`, keys). It is returned to
        the admin UI but NEVER logged and never persisted here.

        Reached through the API-server service proxy like service_contract (the backend
        is off-cluster so it cannot resolve *.svc). The `?raw=1` query cannot go through
        connect_get_namespaced_service_proxy_with_path (it URL-encodes the `?` into the
        path -> 404), so call_api carries it as a real query param. _preload_content=False
        reads raw bytes: the client otherwise coerces the JSON body into a single-quoted
        Python dict repr that json.loads rejects (same footgun as service_contract).
        """
        _validate_name(name)
        svc_obj, ns = None, None
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
            raise GatewayError(404, f"service {name!r} not found")
        port = next((p.port for p in (svc_obj.spec.ports or [])), None)
        proxy_name = f"{name}:{port}" if port else name
        try:
            resp = self.k8s.core.api_client.call_api(
                "/api/v1/namespaces/{namespace}/services/{name}/proxy/{path}",
                "GET",
                path_params={"namespace": ns, "name": proxy_name, "path": "discover"},
                query_params=[("raw", "1")],
                header_params={"Accept": "application/json"},
                auth_settings=["BearerToken"],
                _preload_content=False,
                _return_http_data_only=True,
            )
            http = resp[0] if isinstance(resp, tuple) else resp
            body = http.data
            if isinstance(body, (bytes, bytearray)):
                body = body.decode("utf-8")
            return json.loads(body)
        except GatewayError:
            raise
        except Exception as e:
            # Do not echo the exception body verbatim: on a proxied vendor error it
            # could contain the upstream payload. Keep it short and typed.
            raise GatewayError(502, f"discover?raw=1 failed for {name!r}: {type(e).__name__}")

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

    def _stateful_spec(self, name: str, ns: str) -> dict[str, str] | None:
        """{env, path} for this adapter's runtime-written doc, or None. Bridge keyed by
        image basename (_STATEFUL_DOCS) until the contract's `writable` flag reaches the
        dashboard via a rebuilt /contract."""
        try:
            dep = self.k8s.apps.read_namespaced_deployment(name=name, namespace=ns)
            return _STATEFUL_DOCS.get(_image_basename(dep.spec.template.spec.containers[0].image))
        except Exception:
            return None

    def _writable_doc_path(self, name: str, ns: str) -> str | None:
        spec = self._stateful_spec(name, ns)
        return spec["path"] if spec else None

    def _ensure_writable_store(self, name: str, ns: str = POSITIONING_NS) -> None:
        """PVC-back a stateful adapter's runtime-written doc. The PVC is mounted at a
        DEDICATED DIRECTORY (STORE_DIR) and the service's config-path env is redirected
        to a file inside it — deliberately NOT a subPath mount over the default path: a
        subPath into an empty PVC makes Kubernetes create a DIRECTORY there, which breaks
        a service expecting a file. The service starts with an empty store and creates
        the file on first write (import). Removes any prior subPath store. Idempotent.
        See docs/known-issues/wifi-calibration-subpath-directory.md."""
        spec = self._stateful_spec(name, ns)
        if not spec:
            return
        pvc = f"{name}-data"
        self.k8s.ensure_pvc(ns, pvc)
        fname = spec["path"].rsplit("/", 1)[-1] or "file"
        self.k8s.attach_dir_store(
            ns, name, pvc, STORE_DIR, spec["env"], f"{STORE_DIR}/{fname}",
            strip_paths=(spec["path"],), strip_init=("seed-store",), strip_volumes=("service-files",),
        )

    def _has_writable_store(self, name: str, ns: str = POSITIONING_NS) -> bool:
        """True when the deployment mounts its `<name>-data` PVC at STORE_DIR (the b2
        directory mount). A store present in the OLD subPath shape returns False so the
        reconcile/enable path migrates it. Skips the pod-rolling patch when already b2."""
        try:
            dep = self.k8s.apps.read_namespaced_deployment(name=name, namespace=ns)
            c = dep.spec.template.spec.containers[0]
            return any((m.name == f"{name}-data" and m.mount_path == STORE_DIR)
                       for m in (c.volume_mounts or []))
        except Exception:
            return False

    def enable_persistence(self, name: str) -> dict[str, Any]:
        """Attach the PVC-backed writable store to a stateful adapter (wifi-positioning),
        so a calibration set in its OWN UI survives restart/upgrade. One-click bridge for
        an instance deployed before deploy-time attach; new deploys get it automatically.
        The rollout reuses any existing store (idempotent)."""
        _validate_name(name)
        ns = self._service_namespace(name)
        spec = self._stateful_spec(name, ns)
        if not spec:
            raise ValueError(f"{name} has no runtime-written document to persist")
        self._ensure_writable_store(name, ns)
        return {"status": "persistent", "name": name, "path": f"{STORE_DIR}/{spec['path'].rsplit('/', 1)[-1]}"}

    def apply_service_file(self, name: str, path: str, content: str) -> dict[str, Any]:
        """Store `content` for the document at `path` in the `<name>-files`
        ConfigMap and mount it there (subPath). Idempotent strategic-merge, so
        multiple file-fields accumulate in one ConfigMap / volume. A document the
        service WRITES at runtime (`_STATEFUL_DOCS`) is instead PVC-backed (seeded from
        this ConfigMap) so the writes survive restart/upgrade."""
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

        # Runtime-written doc (e.g. wifi calibration): back it with a PVC (dir mount +
        # env redirect, see _ensure_writable_store) so the service's writes persist,
        # instead of the read-only ConfigMap mount below.
        if self._writable_doc_path(name, ns) == path:
            self._ensure_writable_store(name, ns)
            return {"status": "applied", "name": name, "config_map": cm_name,
                    "mount": path, "persistent": True}

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

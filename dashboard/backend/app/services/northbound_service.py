"""Business logic for the Northbound (positioning/CAMARA) service-management
console. Wraps K8sService to: inventory the northbound services, read the
engine's adapter registry, deploy custom adapter images (they self-register),
and configure a service through its contract.

v0.6.0 adapter model: the engine is the adapter-registry authority. Adapters
SELF-REGISTER (POST /adapters + heartbeat) and the engine evicts dead ones on
TTL. The console no longer owns ADAPTER_URLS; it reads the live registry from the
engine (GET /adapters via the API-server service proxy) and can force-remove a
stale entry (DELETE /adapters/{name}). See docs/architecture/positioning-adapters.md.
"""

import base64
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from kubernetes.client.exceptions import ApiException

from app.config import settings
from app.models import DeployEnvVar, DeployImageRequest
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
# it moved into the env of THAT run only. Nothing is persisted to a file: the repo
# states intent (northbound_image_tags in all.yml) and the cluster states reality, so
# the phase reads the running images and keeps whichever tag is newer. A third,
# machine-local file holding a version is what previously caused a silent downgrade.
# See docs/development/contributing.md "Component image versions".
COMPANION_PREFIX = "ghcr.io/jacobbista/5g-northbound/"
# Phase-10-managed image basename -> the env var its role default reads (lookup env).
# The rest (wifi-adapter, vendor-adapter) are catalog adapters rolled via kubectl set,
# so they need no env var.
COMPANION_TAG_VARS = {
    "positioning-engine": "POSITIONING_ENGINE_TAG",
    "synthetic-adapter": "SYNTHETIC_ADAPTER_TAG",
    "camara-gateway": "CAMARA_GATEWAY_TAG",
    "placement-editor": "PLACEMENT_EDITOR_TAG",
    "location-app": "LOCATION_APP_TAG",
}
PHASE_MANAGED_BASENAMES = set(COMPANION_TAG_VARS)
CATALOG_BASENAMES = {"wifi-adapter", "vendor-adapter"}
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
ENGINE_SERVICE = "positioning-engine"
ENGINE_PORT = 8080
CAMARA_NS = "camara"
GATEWAY_SERVICE = "camara-gateway"
GATEWAY_PORT = 8080

# Per-service contract metadata (kind, configurable) cached by (name, image) so
# the 5s inventory poll does not re-fetch /contract every time. Invalidated when
# the image changes.
_CONTRACT_META: dict[tuple, dict] = {}

# Deployments phase 10 owns (deployment -> namespace): their image and wiring come
# from all.yml, so "Update all" re-runs the phase instead of patching them here.
MANAGED_DEPLOYMENTS = {
    "camara-gateway": "camara",
    "positioning-engine": "positioning",
    "location-app": "mec",
}

# Consumers whose <field> should point at a deployed adapter of <kind> (the
# adapter image's basename). Used to auto-detect a newly deployed adapter that is
# not yet wired into the consumer and offer a one-click bind (binding_suggestions).
_ADAPTER_BINDINGS = {
    "placement-editor": [
        {"field": "VENDOR_ADAPTER_URL", "kind": "vendor-adapter"},
        {"field": "WIFI_ADAPTER_URL", "kind": "wifi-adapter"},
    ],
    "camara-gateway": [
        {"field": "WIFI_ADAPTER_URL", "kind": "wifi-adapter"},
    ],
}


# Runtime choices an adapter image declares in its /contract as `<name>s` (the
# set it implements) next to `<name>` (the one active now), and accepts on
# `PUT /bindings {<name>: value}` with a 422 for anything outside the set.
# `transport` follows the same shape but is chosen by the schema document, so it
# is reported elsewhere and excluded here.
_BINDING_KEYS = ("motion_model", "algorithm")


def _contract_choices(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key in _BINDING_KEYS:
        options = contract.get(key + "s")
        if isinstance(options, list) and options:
            out[key] = {"options": [str(o) for o in options], "active": contract.get(key)}
    return out


def _kelt_wiring(name: str, port: int) -> dict[str, str]:
    """The registration env KELT writes so an adapter reaches the engine and
    announces who it is: POSITIONING_ENGINE_URL and ADAPTER_BASE_URL are derived
    from cluster facts (an adapter image cannot know its own Service URL), and
    ADAPTER_NAME is the deployment's own identity (baked into ADAPTER_BASE_URL and
    into what the engine's DEVICE_MAP must match), so none of the three is something
    the operator revisits without redeploying. What the adapter IS comes from the
    image (its adapter.contract.yaml `adapter:` family, read at registration since
    0.17.1) and from ADAPTER_CAPABILITIES (the bound source), never from here.

    Single owner for the deploy path, the upgrade merge and the config report, so
    the three locked vars cannot drift.
    """
    return {
        "POSITIONING_ENGINE_URL": f"http://{ENGINE_SERVICE}.{POSITIONING_NS}.svc.cluster.local:{ENGINE_PORT}",
        "ADAPTER_NAME": name,
        "ADAPTER_BASE_URL": f"http://{name}.{POSITIONING_NS}.svc.cluster.local:{port}",
    }


# Env the config UI hides as deploy-settled identity: renaming or re-pointing any of
# these is effectively redeploying the adapter, not adjusting a setting.
KELT_WIRED_ENV = ("POSITIONING_ENGINE_URL", "ADAPTER_NAME", "ADAPTER_BASE_URL")
# Removed upstream in 0.17.1 (no consumer; the image declares its own family). An
# adapter deployed earlier may still carry it in <name>-config; the upgrade drops it
# so the contract validator does not flag a dead variable.
_RETIRED_ADAPTER_ENV = ("ADAPTER_KIND",)

# Env the northbound adapter model reads but an adapter's own /contract does not
# declare. vendor-adapter 0.17.0 documented ADAPTER_CAPABILITIES as the deploy-time
# channel for the bound source's traits without listing it in env.contract.yaml;
# 0.17.1 declares it (type: json), at which point this entry is skipped because
# _kelt_declared_env only adds what the contract lacks. Kept for an adapter still
# on 0.17.0, marked `declared_by: kelt`, adapter role only. Drop once none remains.
_KELT_DECLARED_ENV: dict[str, list[dict[str, Any]]] = {
    "adapter": [{
        "name": "ADAPTER_CAPABILITIES",
        "type": "json",
        "default": "",
        "sensitive": False,
        "writable": True,
        "declared_by": "kelt",
        "description": (
            "Traits of the source this adapter is bound to, as JSON, merged over the "
            "image's baked adapter.contract.yaml and sent to the engine on every "
            "heartbeat: source, kinds, frame, z, accuracy_class (band the technology "
            "nominally delivers) and nominalAccuracy (metres; used when a fix reports "
            "no radius). A generic image such as vendor-adapter holds none of these, so "
            "without this an adapter reports no source and its fixes are dropped."
        ),
    }],
}


def _kelt_declared_env(role: str, contract: dict[str, Any]) -> list[dict[str, Any]]:
    """KELT-declared entries for this role that the service's /contract does not
    already name. Empty for every non-adapter role."""
    declared = {
        e.get("name")
        for grp in ("required", "recommended", "optional")
        for e in (contract.get("env", {}).get(grp) or [])
    }
    return [dict(e) for e in _KELT_DECLARED_ENV.get(role, []) if e["name"] not in declared]


def _image_basename(image: str | None) -> str:
    """ghcr.io/jacobbista/5g-northbound/vendor-adapter:0.8.6 -> vendor-adapter."""
    return (image or "").rsplit("/", 1)[-1].split("@")[0].split(":")[0]


# Where a service sits in the data path, for the Services view. `role` is declared at
# deploy time as the `kelt.io/role` label (playbook for phase-managed services, dashboard
# for catalog adapters); the name heuristic is only a fallback until every workload carries
# the label. `lane` groups roles into the south -> core -> north flow.
_LANE_BY_ROLE = {"adapter": "south", "engine": "core", "gateway": "north", "app": "north", "tool": "north", "proxy": "north"}


def _role_of(name: str, labels: dict) -> str:
    role = (labels or {}).get("kelt.io/role")
    if role:
        return role
    if (labels or {}).get("app.kubernetes.io/managed-by") == "dashboard-northbound":
        return "adapter"
    n = (name or "").lower()
    if "engine" in n:
        return "engine"
    if "gateway" in n:
        return "gateway"
    if "oauth2-proxy" in n or n.endswith("-proxy"):
        return "proxy"
    if "adapter" in n:
        return "adapter"
    if "placement" in n:
        return "tool"
    return "app"


# An adapter's subtitle is not a fallback string: the Services view composes it live
# from the engine registry (image family, declared source) and the contract
# (transport), so the role default stays empty until the adapter registers.
_SUBTITLE_BY_ROLE = {"gateway": "CAMARA API", "engine": "fusion", "adapter": "", "app": "app", "tool": "tool", "proxy": "front-door auth"}
# KELT's own catalog adapters carry no annotation until re-deployed, so their copy falls
# back by name. A vendor's own adapter (deployed by the operator) is NOT hardcoded here:
# it declares its subtitle from the adapter kind at deploy time, else this generic default.
_ADAPTER_COPY = {
    "wifi": ("wifi", "RSSI trilateration"),
    "synthetic": ("synthetic", "engine-driven demo track"),
}


def _copy_of(name: str, role: str, annotations: dict) -> tuple[str, str]:
    """A short subtitle for the role chip and a one-line description for the row. Both are
    declared at deploy time (kelt.io/subtitle, kelt.io/description); the fallback keeps a
    sensible label until the annotation lands."""
    ann = annotations or {}
    subtitle = ann.get("kelt.io/subtitle")
    description = ann.get("kelt.io/description")
    if subtitle is None or description is None:
        n = (name or "").lower()
        fb_sub, fb_desc = _SUBTITLE_BY_ROLE.get(role, role), ""
        for key, (s, d) in _ADAPTER_COPY.items():
            if key in n:
                fb_sub, fb_desc = s, d
                break
        if subtitle is None:
            subtitle = fb_sub
        if description is None:
            description = fb_desc
    return subtitle, (description or "")


def _adapter_probes(port: int) -> dict[str, Any]:
    """Readiness on /ready (config-aware: 503 + reason while degraded, e.g. a
    vendor-adapter with no schema, so the dashboard shows it NOT ready instead of falsely
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
    """...vendor-adapter:0.8.6 -> 0.8.6 (empty when digest-pinned or untagged)."""
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
# default path, so no subPath is needed. wifi-adapter persists its calibration
# (tx_power/path_loss_n) into WIFI_CONFIG_PATH. SCHEMA_FILE is writable upstream but
# operator-authored here (no runtime writer), so it stays a ConfigMap.
# See docs/architecture/positioning-adapters.md and
# docs/known-issues/wifi-calibration-subpath-directory.md.
_STATEFUL_DOCS = {
    "wifi-adapter": {"env": "WIFI_CONFIG_PATH", "path": "/app/config/wifi-config.json"},
}
# Dedicated directory the writable PVC is mounted at (whole-dir mount, no subPath).
STORE_DIR = "/data"


def _is_file_field(name: str | None, path: str | None) -> bool:
    """A document the operator/portal PROVIDES, by convention named *_FILE with an
    absolute-path value (e.g. the vendor-adapter's SCHEMA_FILE). Deliberately NOT
    *_PATH: those (e.g. the engine's BLUEPRINT_SEED_PATH) are paths the service
    READS from a managed/distributed source, not an operator document — those are a
    seed/distribution concern, not a paste-a-file one."""
    return bool(name) and name.endswith("_FILE") and bool(path) and path.startswith("/")

# DNS-1123 label for adapter/service names.
_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
# image[:tag] or image@sha256:...; rejects spaces and shell metacharacters.
_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/:@]{0,255}$")
# A keyword match on a log line, for log_health(). Deliberately loose (a "go look"
# signal, not a diagnosis): the case that motivated this is a pod reporting
# Ready, registered, serving every OTHER request fine, while silently 500ing on
# one specific asset (the engine's ZeroDivisionError on a zero-accuracy fusion,
# 2026-09-11) - nothing in its health/rollout state hinted at that.
_LOG_ERROR_RE = re.compile(r"error|exception|traceback", re.IGNORECASE)

# The static adapter HTTP contract surfaced in the UI's guidance panel.
MEASUREMENT_SCHEMA = {
    "source": "wifi",
    "frame": "local",
    "x": 11.5,
    "y": 0.0,
    "z": 10.3,
    "accuracy": 6.6,
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
    accuracy: float
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
        push_map = settings.n6m_push_adapter_map()
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
                # location-app; exclude them so this console lists only
                # positioning/CAMARA services. See docs/architecture/edge-apps.md.
                if labels.get("app.kubernetes.io/managed-by") == "dashboard-apps":
                    continue
                containers = dep.spec.template.spec.containers or []
                image = containers[0].image if containers else None  # deployment SPEC image (intent)
                # The deployment's pods (by ReplicaSet owner, not a name-prefix guess).
                # Each pod carries the image it ACTUALLY runs and whether it is ready or
                # crashlooping — the truth that the spec image and ready_replicas hide.
                dep_pods = [p for p in pods.values() if p.deployment == name]
                # A pod running the CURRENT spec image is the target of this rollout; one on
                # a different image is an old pod being torn down. Only the TARGET failing is
                # "degraded" — an old crashlooping pod on its way out, or the old pod still
                # serving while the new one comes up cleanly, is a transient "progressing"
                # (so a healthy upgrade clears the red promptly instead of lingering).
                st = dep.status
                desired = dep.spec.replicas or 0
                current_pods = [p for p in dep_pods if p.image == image]
                current_ready = [p for p in current_pods if p.ready]
                current_bad = [p for p in current_pods
                               if p.waiting_reason in ("CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull")
                               or (not p.ready and p.restarts > 0)]
                # Serving image = a ready target-image pod once the rollout lands, else
                # whatever ready pod is still up (the old one, mid-transition or failed).
                ready_pods = [p for p in dep_pods if p.ready]
                running_image = (current_ready[0].image if current_ready
                                 else ready_pods[0].image if ready_pods else None)
                progress_failed = any(
                    c.type == "Progressing" and c.status == "False"
                    for c in (st.conditions or [])
                )
                if current_bad or progress_failed:
                    rollout = "degraded"          # the image being deployed is failing
                elif len(current_ready) < desired:
                    rollout = "progressing"       # target pods still coming up (old may still serve)
                else:
                    rollout = "complete"
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
                            # A vendor-payload adapter declares a mapping grammar (the contract
                            # points at /contract/schema); that gets the field-mapping studio.
                            "has_mapping": bool(contract.get("schema")),
                        }
                        _CONTRACT_META[(name, image)] = meta
                    else:
                        meta = {"kind": None, "configurable": False, "subdomain": None, "has_mapping": False}
                        # Cache the "no contract" result only once the pod is Ready:
                        # a ready pod that still has no /contract genuinely has none,
                        # so stop re-probing it every poll (avoids a slow inventory).
                        # A not-yet-Ready pod is left uncached so the next poll retries
                        # until it serves its contract (just-deployed adapters).
                        if ready:
                            _CONTRACT_META[(name, image)] = meta
                # Stateful adapters (wifi-adapter) write a doc at runtime that must be
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
                role = _role_of(name, labels)
                subtitle, description = _copy_of(name, role, dep.metadata.annotations or {})
                services.append({
                    "name": name,
                    "namespace": ns,
                    "image": image,               # deployment spec (intent)
                    "running_image": running_image,  # what a ready pod actually runs (reality)
                    "rollout": rollout,           # complete | progressing | degraded
                    "replicas": desired,
                    "ready_replicas": st.ready_replicas or 0,
                    "managed": name in MANAGED_DEPLOYMENTS,
                    "labels": labels,
                    "role": role,
                    "lane": _LANE_BY_ROLE.get(role, "north"),
                    "subtitle": subtitle,
                    "description": description,
                    "node_port": node_ports.get(name),
                    "kind": meta["kind"],
                    "configurable": meta["configurable"],
                    "has_mapping": meta["has_mapping"],
                    "subdomain": meta["subdomain"],
                    "stateful": stateful,
                    "persistent": persistent,
                    "pods": [{"name": p.name, "phase": p.phase, "restarts": p.restarts,
                              "ready": p.ready, "waiting_reason": p.waiting_reason, "image": p.image}
                             for p in dep_pods],
                    # Push adapters carry a reserved n6m address: the 5G-reachable
                    # ingest an edge scanner POSTs to. Bare IP (no mask), present
                    # only when configured.
                    "n6m_ip": (push_map[name].split("/", 1)[0] if name in push_map else None),
                })
        return {"services": services}

    # ── Log health (best-effort "go look" signal, not a diagnosis) ─────────────
    def log_health(self) -> dict[str, Any]:
        """Per deployment, whether its pod's recent log tail contains an error
        keyword. Ready/rollout state alone misses a pod that is up, registered,
        and serving most requests fine while silently 500ing on one - a caught
        exception fails a request, not a liveness probe. Meant to be polled far
        slower than inventory() (log reads are real calls per pod); the UI marks
        `logs` on a hit instead of adding a whole new poll surface to watch."""
        out: dict[str, Any] = {}
        for ns in NORTHBOUND_NAMESPACES:
            try:
                pods = self.k8s.list_pods(ns)
            except Exception:
                continue
            seen: set[str] = set()
            for p in pods:
                if not p.deployment or p.deployment in seen:
                    continue
                seen.add(p.deployment)
                try:
                    text = self.k8s.pod_logs(ns, p.name, tail_lines=100)
                except Exception:
                    continue
                hit = next((line for line in text.splitlines() if _LOG_ERROR_RE.search(line)), None)
                if hit:
                    out[p.deployment] = {"has_errors": True, "sample": hit.strip()[:200]}
        return out

    # ── Adapter registry (engine = authority; adapters self-register) ──────────
    def list_adapters(self) -> list[dict[str, Any]]:
        """The live registry from the engine (GET /adapters via the API-server
        service proxy). Each entry carries membership + reachability: name, kind,
        baseUrl, registeredVia (self|seed|manual), lastSeenSAgo, failCount /
        inCooldown, and a derived state (live|unreachable|stale). Degrades to an
        empty list when the engine is briefly unreachable (e.g. mid-rollout)."""
        try:
            raw = self.k8s.service_proxy_get(POSITIONING_NS, ENGINE_SERVICE, ENGINE_PORT, "adapters")
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
            adapters = data.get("adapters", [])
        except Exception:
            return []
        # Annotate push adapters with their 5G ingest address so the UI can show
        # the edge scanner where to POST. The IP is the reserved n6m address; the
        # scanner reaches it over the 5G user plane (via the UPF), not the ClusterIP.
        push = settings.n6m_push_adapter_map()
        for a in adapters:
            cidr = push.get(a.get("name"))
            if cidr:
                a["n6m_ip"] = cidr.split("/", 1)[0]
        return adapters

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
        set (load-all, edit, save-all); the gateway validates against asset.schema.json.

        Since engine 0.8.19 the live broadcast set (and each device's source) is derived
        from the adapters' `devices` capability, so an onboarded asset appears on the map
        as soon as its adapter reports it. No DEVICE_IDS bridge is needed."""
        try:
            resp = httpx.put(f"{self._gateway_base_url()}/assets", headers=self._bearer(token), json=body, timeout=8.0)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GatewayError(exc.response.status_code, exc.response.text)
        assets = list((body or {}).get("assets") or [])
        return {
            "status": "applied",
            "count": len(assets),
        }

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
        for k, v in _kelt_wiring(name, port).items():
            data.setdefault(k, v)
        retired = [k for k in _RETIRED_ADAPTER_ENV if k in data]
        for k in retired:
            data.pop(k)
        self.k8s.apply_configmap(POSITIONING_NS, cm_name, data)
        if retired:
            self.k8s.unset_configmap_keys(POSITIONING_NS, cm_name, retired)
        # Patch the image AND ensure envFrom binds the config/secret (an adapter
        # deployed before v0.6.0 may bind neither, so the merged self-reg env would
        # never reach the pod). Inline env and volumes are left untouched.
        self.k8s.set_workload_image(POSITIONING_NS, name, image, envfrom=[cm_name, f"{name}-secrets"],
                                    probes=_adapter_probes(port))
        # Stateful adapters (wifi-adapter writes its calibration) get a PVC-backed
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

    def _ansible_env(self, tag_overrides: dict[str, str] | None = None) -> dict[str, str]:
        # Source the operator's persisted config so phase 10 re-renders exactly the
        # surfaces they have enabled (rolling them to the pinned images) and does NOT
        # turn on ones they disabled. No forced enable flag.
        env = {**os.environ, "ANSIBLE_CONFIG": ANSIBLE_CFG}
        env.update(self._source_env_file(TESTBED_ENV))
        env.update(self._source_env_file(TESTBED_SECRETS))
        # Per-image release tags this run is rolling forward to. They apply to this
        # subprocess only; the resulting Deployment is what a later run reads back.
        env.update(tag_overrides or {})
        return env

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
        tags into the phase-10 run env and re-runs it (only when a phase-managed image
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
            env = self._ansible_env(overrides)
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

    # ── Deploy-from-image ─────────────────────────────────────────────────────
    def _apply_workload(self, ns: str, name: str, image: str, port: int, env, image_pull_secret, annotations=None) -> None:
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

        labels = {"app": name, "app.kubernetes.io/managed-by": "dashboard-northbound", "kelt.io/role": "adapter"}
        # Push adapters (edge scanner POSTs over 5G) are useless without an n6m
        # foothold, so attach one automatically at the reserved IP from config.
        # Cross-namespace NAD reference: the adapter lives here (positioning) but
        # the n6m-net NAD lives in the mec namespace. Pull adapters are not in the
        # map and get no n6m interface. See docs/architecture/positioning-adapters.md.
        pod_meta: dict[str, Any] = {"labels": labels}
        n6m_cidr = settings.n6m_push_adapter_map().get(name)
        if n6m_cidr:
            net = {
                "name": settings.n6m_nad_name,
                "namespace": settings.n6m_nad_namespace,
                "interface": "n6m",
                "ips": [n6m_cidr],
            }
            pod_meta["annotations"] = {"k8s.v1.cni.cncf.io/networks": json.dumps([net])}
        self.k8s.upsert_deployment(ns, {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": name, "namespace": ns, "labels": labels,
                         **({"annotations": annotations} if annotations else {})},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": name}},
                "template": {"metadata": pod_meta, "spec": pod_spec},
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
        self_reg = [DeployEnvVar(name=k, value=v) for k, v in _kelt_wiring(req.name, req.port).items()]
        have = {e.name for e in req.env}
        env = list(req.env) + [e for e in self_reg if e.name not in have]
        self._apply_workload(POSITIONING_NS, req.name, req.image, req.port, env, req.image_pull_secret)
        # Stateful adapters (wifi-adapter writes its calibration at runtime) get a
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
        # Liveness first: a proxy call to a pod that is not running fails as a
        # connection error or a 503 from the API server, and that exception's own
        # __str__ (an ApiException embeds the raw response, headers included) is
        # not something to hand an operator - it read as a stack trace, not a
        # reason (found live, 2026-09-11: wittra scaled to 0 for a G5 experiment
        # showed exactly this in the info panel). Ask the Deployment first and say
        # why in the same terms the Services panel already uses, instead of
        # attempting the call and leaking internals when it predictably fails.
        try:
            dep = self.k8s.apps.read_namespaced_deployment(name=name, namespace=ns)
            desired = dep.spec.replicas or 0
            ready = dep.status.ready_replicas or 0
            if desired == 0:
                return {"available": False, "service": name, "namespace": ns,
                        "error": "stopped (scaled to 0 replicas)"}
            if ready == 0:
                reason = next(
                    (p.waiting_reason for p in self.k8s.list_pods(ns)
                     if p.deployment == name and p.waiting_reason),
                    None,
                )
                return {"available": False, "service": name, "namespace": ns,
                        "error": f"not ready ({reason})" if reason else "not ready (starting up)"}
        except Exception:
            pass  # Deployment lookup itself failed; fall through and let the proxy call try anyway.
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
        except ApiException as e:  # sanitized: exc.status/.reason, never the raw response dump
            return {"available": False, "service": name, "namespace": ns,
                    "error": f"contract endpoint unreachable (HTTP {e.status} {e.reason})" if e.status
                             else "contract endpoint unreachable"}
        except Exception:  # connection error, 404 (no endpoint yet), parse error
            return {"available": False, "service": name, "namespace": ns,
                    "error": "contract endpoint unreachable"}

    def discover_raw(self, name: str) -> dict[str, Any]:
        """Raw vendor device records from an adapter's GET /discover?raw=1, for the
        guided classify builder (operator sees the vendor's native field names to
        author the mapping + classify predicates).

        ADMIN-ONLY at the router: the raw payload is the vendor's own record and can
        carry network secrets (radio network ids, keys). It is returned to
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

    def contract_schema(self, name: str) -> dict[str, Any]:
        """The JSON Schema of an adapter's mapping DOCUMENT (GET /contract/schema on the
        adapter), so the guided mapping builder reads the grammar (PathSpec/ConstSpec,
        the transform union, DiagnosticsBlock) at runtime instead of hardcoding it.

        Reached through the API-server service proxy like discover_raw. The `/schema`
        subpath must survive: it is baked into the proxy path template LITERALLY, because
        a `{path}` param URL-encodes the `/` to %2F and the proxy 404s on it."""
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
                "/api/v1/namespaces/{namespace}/services/{name}/proxy/contract/schema",
                "GET",
                path_params={"namespace": ns, "name": proxy_name},
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
            raise GatewayError(502, f"contract/schema failed for {name!r}: {type(e).__name__}")

    def diagnostics_vocabulary(self) -> dict[str, Any]:
        """The gateway's published core diagnostics vocabulary (no-auth contract). The
        guided mapping builder reads the core targets/units/tiers from here so a 5th core
        field is a spec change on the gateway, never a hand-edit in the dashboard."""
        try:
            resp = httpx.get(f"{self._gateway_base_url()}/contracts/diagnostics-vocabulary.json", timeout=6.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise GatewayError(502, f"vocabulary fetch failed: {type(e).__name__}")

    def accuracy_class_vocabulary(self) -> dict[str, Any]:
        """The gateway's published accuracy-class bands (no-auth contract, 0.17.0+).
        The capabilities editor offers `accuracy_class` from here, and shows each
        band's bounds, instead of hardcoding sub-metre/metre/coarse."""
        try:
            resp = httpx.get(f"{self._gateway_base_url()}/contracts/accuracy-class-vocabulary.json", timeout=6.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise GatewayError(502, f"vocabulary fetch failed: {type(e).__name__}")

    # Where a deployment's env comes from, in precedence order (a later envFrom
    # source wins, inline `env` beats them all). `<name>-config` / `<name>-secrets`
    # are the operator's (the Configure form writes them); anything else, and
    # inline env, is deployment wiring the operator does not edit from here.
    # Read from the live Deployment, never inferred from the service's name.
    def _env_sources(self, dep: Any, ns: str, name: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if dep is None:
            return out
        cont = dep.spec.template.spec.containers[0]
        for ef in (cont.env_from or []):
            if ef.config_map_ref:
                cm = ef.config_map_ref.name
                try:
                    data = dict(self.k8s.get_configmap(ns, cm).get("data") or {})
                except Exception:
                    data = {}
                out.append({"kind": "configmap", "name": cm, "data": data,
                            "operator": cm == f"{name}-config"})
            if ef.secret_ref:
                sec_name = ef.secret_ref.name
                try:
                    sec = self.k8s.core.read_namespaced_secret(name=sec_name, namespace=ns)
                    data = {k: None for k in (sec.data or {})}
                except Exception:
                    data = {}
                out.append({"kind": "secret", "name": sec_name, "data": data,
                            "operator": sec_name == f"{name}-secrets"})
        inline = {e.name: e.value for e in (cont.env or []) if e.name}
        if inline:
            out.append({"kind": "inline", "name": "deployment", "data": inline, "operator": False})
        return out

    @staticmethod
    def _effective(sources: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
        """The source that actually provides `key` (the last one that has it)."""
        hit = None
        for src in sources:
            if key in src["data"]:
                hit = src
        return hit

    def set_binding(self, name: str, key: str, value: str) -> dict[str, Any]:
        """Change one runtime choice on an adapter through its own PUT /bindings
        (API-server service proxy, like discover_raw). The value is checked against
        the contract's declared set first, so a typo never reaches the pod; the
        adapter validates again and answers 422 on anything it does not implement."""
        # service_contract() wraps the actual /contract body one level down, in
        # ["contract"] (service_config() unwraps the same way); this used to skip
        # that and look for the choices in the wrapper, which never has them.
        c = self.service_contract(name)
        if not c.get("available"):
            raise ValueError(f"{name!r} is not reachable right now ({c.get('error') or 'try again'})")
        contract = c["contract"]
        choices = _contract_choices(contract)
        if key not in choices:
            raise ValueError(f"{name!r} declares no choice named {key!r}")
        if value not in choices[key]["options"]:
            raise ValueError(f"{value!r} is not one of {choices[key]['options']} for {key}")
        _validate_name(name)
        ns = self._service_namespace(name)
        try:
            svc_obj = self.k8s.core.read_namespaced_service(name=name, namespace=ns)
        except Exception:
            raise GatewayError(404, f"service {name!r} not found")
        port = next((p.port for p in (svc_obj.spec.ports or [])), None)
        proxy_name = f"{name}:{port}" if port else name

        def _proxy(method: str, path: str, body: Any = None) -> Any:
            resp = self.k8s.core.api_client.call_api(
                "/api/v1/namespaces/{namespace}/services/{name}/proxy/{path}",
                method,
                path_params={"namespace": ns, "name": proxy_name, "path": path},
                body=body,
                header_params={"Accept": "application/json", "Content-Type": "application/json"},
                auth_settings=["BearerToken"],
                _preload_content=False,
                _return_http_data_only=True,
            )
            http = resp[0] if isinstance(resp, tuple) else resp
            raw = http.data.decode("utf-8") if isinstance(http.data, (bytes, bytearray)) else http.data
            return json.loads(raw) if raw else None

        try:
            # PUT /bindings on this adapter REPLACES its whole stored config
            # instead of merging (found live 2026-09-15: changing just `algorithm`
            # wiped the AP bindings and calibration samples already on disk). Read
            # the full current object first and send it back with only the one key
            # changed, so a field this call never meant to touch cannot be lost.
            current = _proxy("GET", "bindings") or {}
            current[key] = value
            result = _proxy("PUT", "bindings", current)
            return {"service": name, key: value, "adapter": result}
        except GatewayError:
            raise
        except Exception as e:
            status = getattr(e, "status", None)
            if status == 422:
                raise ValueError(f"the adapter refused {value!r} for {key}")
            raise GatewayError(502, f"PUT /bindings failed for {name!r}: {type(e).__name__}")

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
        dep = None
        try:
            dep = self.k8s.apps.read_namespaced_deployment(name=name, namespace=ns)
        except Exception:
            pass
        sources = self._env_sources(dep, ns, name)

        def file_state(path: str, probe_pod: bool = True) -> str:
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
            # Paths that are not operator documents skip the exec: "internal" =
            # the service's own file (shipped in the image or written by it).
            if not probe_pod:
                return "internal"
            return "ephemeral" if self._read_pod_file(name, ns, path) else "absent"
        def annotate(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
            out = []
            for e in entries or []:
                n = e.get("name")
                src = self._effective(sources, n)
                item = {**e, "set": src is not None}
                if not e.get("sensitive"):
                    item["value"] = src["data"].get(n) if src else None  # never expose a secret value
                # Who owns the value: the operator (Configure writes <name>-config /
                # <name>-secrets, or nothing provides it yet) or the deployment (wiring
                # ConfigMap, inline env). A deployment-owned key would shadow whatever
                # Configure wrote, so the form does not offer it.
                item["owner"] = "operator" if (src is None or src["operator"]) else "deployment"
                item["source"] = src["name"] if src else None
                # A path-typed entry maps to a volume: say how it is provided so the UI
                # can show storage instead of an input. *_FILE documents the dashboard
                # can own get the full state (editor + readiness), other paths only the
                # mount kind.
                fpath = (item.get("value") if not e.get("sensitive") else None) or e.get("default") or ""
                stateful_restorable = fpath and fpath in self._writable_doc_path(name, ns)
                if _is_file_field(n, fpath):
                    item["file_state"] = file_state(fpath)
                    item["file_path"] = fpath
                elif str(e.get("type") or "").lower() == "path":
                    if stateful_restorable:
                        # A *_PATH the service writes itself is normally storage,
                        # not an operator document (see _is_file_field). This one is
                        # the exception: it is PVC-backed specifically so the
                        # dashboard's own apply_service_file can seed/restore it
                        # (e.g. after an upstream write wipes it), so it gets the
                        # same editor as a *_FILE field instead of read-only info.
                        item["file_state"] = "managed"
                    else:
                        item["file_state"] = file_state(fpath, probe_pod=False) if fpath else "unset"
                    item["file_path"] = fpath
                # Adapter-registration wiring (see _kelt_wiring): either derived from
                # cluster facts or fixed by the deploy form. Neither is something the
                # operator revisits in a config form, so the UI leaves it out.
                if n in KELT_WIRED_ENV:
                    item["managed"] = True
                out.append(item)
            return out

        env = dict(contract.get("env", {}))
        role = _role_of(name, (dep.metadata.labels if dep is not None else None) or {})
        extra = _kelt_declared_env(role, contract)
        if extra:
            env["optional"] = list(env.get("optional") or []) + extra
        annotated = {grp: annotate(env.get(grp)) for grp in ("required", "recommended", "optional")}
        hints = None
        if any(e.get("name") == "ADAPTER_CAPABILITIES" for grp in annotated.values() for e in grp):
            schema_doc = next((e for grp in annotated.values() for e in grp if e.get("role") == "schema"), None)
            hints = self._capabilities_hints(name, (schema_doc or {}).get("file_path") or "")
        return {
            "capabilities_hints": hints,
            # vendor-adapter 0.17.1+: how the adapter reaches its source. `transports`
            # is what the image implements, `transport` what the active schema chose.
            # One implemented transport is a fact the UI states, not a choice it offers.
            "transports": contract.get("transports"),
            "transport": contract.get("transport"),
            # Runtime choices the image declares as a list plus an active value
            # (wifi-adapter 0.17.3: motion_models/motion_model, algorithms/algorithm),
            # set through the adapter's own PUT /bindings, effective on the next
            # scan, no restart. Listed from the contract, never typed.
            "bindings": _contract_choices(contract),
            "available": True,
            "service": name,
            "namespace": ns,
            "kind": contract.get("kind"),
            "external_origin": contract.get("external_origin"),
            "description": contract.get("description"),
            "config_map": f"{name}-config",
            "secret": f"{name}-secrets",
            # Passed through from the adapter's own /contract (vendor-adapter 0.14.0+).
            # `configured` false means it holds no schema, so its vendor env list is
            # empty and the UI asks for a schema instead of credentials. `mapping`
            # carries supported/mapped/unmapped, which is how the UI can say that the
            # adapter emits a field the mounted schema does not map. A service that
            # predates these fields simply reports None and the UI falls back.
            "configured": contract.get("configured"),
            "vendor": contract.get("vendor"),
            "schema_source": contract.get("schema_source"),
            "mapping": contract.get("mapping"),
            "discover_mapping": contract.get("discover_mapping"),
            "env": annotated,
        }

    def _capabilities_hints(self, name: str, schema_path: str) -> dict[str, Any]:
        """What is already known about the source an adapter is bound to, so the
        capabilities editor opens filled in and the operator confirms instead of
        typing from memory. Two sources, both live: what the adapter currently
        advertises in the engine registry (its baked base merged with any env), and
        what its mounted schema already states (the vendor name, the coordinate
        frame it maps, whether it maps a height). Nothing here is guessed from names."""
        out: dict[str, Any] = {"advertised": None, "schema": None}
        for a in self.list_adapters():
            host = str(a.get("baseUrl") or "").replace("http://", "").split(".")[0]
            if a.get("name") == name or host == name:
                out["advertised"] = a.get("capabilities") or None
                break
        if schema_path:
            try:
                doc = json.loads(self.get_service_file(name, schema_path).get("content") or "")
                mapping = doc.get("mapping") or {}
                out["schema"] = {
                    "vendor": doc.get("vendor"),
                    "frame": (mapping.get("frame") or {}).get("const"),
                    "z": "y" in mapping,
                }
            except Exception:
                pass
        return out

    def apply_service_config(self, name: str, values: dict[str, str | None]) -> dict[str, Any]:
        """Single-mechanism apply: route each var by the contract's `sensitive`
        flag to a Secret (sensitive) or ConfigMap (not), both consumed via the
        deployment's envFrom, then rollout so the pod re-reads them (and, for
        frontends, the image entrypoint re-renders env-config.js).

        Both writes are strategic-merge patches, so untouched keys are preserved.
        The deployment must list `<name>-config` / `<name>-secrets` in envFrom after
        its wiring sources (the manifests do, with optional: true so a degraded pod
        still boots); this manages the content, not the wiring.
        """
        c = self.service_contract(name)
        if not c.get("available"):
            raise ValueError(f"{name} exposes no /contract; refusing to map config blindly")
        ns = c["namespace"]
        contract = c["contract"]
        dep = None
        try:
            dep = self.k8s.apps.read_namespaced_deployment(name=name, namespace=ns)
        except Exception:
            pass
        role = _role_of(name, (dep.metadata.labels if dep is not None else None) or {})
        sensitive: dict[str, bool] = {}
        for grp in ("required", "recommended", "optional"):
            for e in (contract.get("env", {}).get(grp) or []):
                sensitive[e["name"]] = bool(e.get("sensitive"))
        for e in _kelt_declared_env(role, contract):
            sensitive[e["name"]] = bool(e.get("sensitive"))
        unknown = [k for k in values if k not in sensitive]
        if unknown:
            raise ValueError(f"vars not in {name} contract: {sorted(unknown)}")
        # A JSON-typed var (contract `type: json`, 0.17.1+) must at least parse: the
        # adapter swallows a malformed ADAPTER_CAPABILITIES silently (falls back to the
        # baked base) and the operator would only notice as a source that never shows up.
        json_typed = {
            e["name"] for grp in ("required", "recommended", "optional")
            for e in (contract.get("env", {}).get(grp) or []) + _kelt_declared_env(role, contract)
            if str(e.get("type") or "").lower() == "json"
        }
        for k in json_typed:
            v = values.get(k)
            if v:
                try:
                    if not isinstance(json.loads(v), dict):
                        raise ValueError
                except ValueError:
                    raise ValueError(f"{k} must be a JSON object")
        # Writes go to the operator's own objects. A key the deployment provides
        # elsewhere (wiring ConfigMap, inline env) is refused: writing it here would
        # either be shadowed or fight the phase that rewrites the wiring.
        cm_name, secret_name = f"{name}-config", f"{name}-secrets"
        sources = self._env_sources(dep, ns, name)
        owned_elsewhere = sorted(
            k for k in values
            if (src := self._effective(sources, k)) is not None and not src["operator"]
        )
        if owned_elsewhere:
            raise ValueError(f"{owned_elsewhere} are set by the deployment (all.yml), not from here")
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
        field is single-valued (e.g. placement-editor's VENDOR_ADAPTER_URL points at
        ONE vendor-adapter), so >1 candidate is a choice, not an auto-bind."""
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
    # A contract var named like a path (`*_FILE`, e.g. the vendor-adapter's
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

    def _write_pod_file(self, name: str, ns: str, path: str, content: str) -> bool:
        """Write `content` verbatim into a running pod at `path`, exec'd with the
        payload base64'd on the command line (no stdin streaming to manage). Only
        called for a path apply_service_file already checked is this service's own
        PVC-backed store (see _writable_doc_path) — attach_dir_store mounts the PVC
        and redirects the env to it, but copies nothing onto it by itself, so this
        is the step that actually puts the content where the service reads it."""
        if not re.match(r"^/[\w./-]+$", path or ""):
            return False
        try:
            pods = self.k8s.core.list_namespaced_pod(namespace=ns, label_selector=f"app={name}").items
            pod = next((p.metadata.name for p in pods if p.status.phase == "Running"), None)
            if not pod:
                return False
            b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
            out = self.k8s.exec_in_pod(
                ns, pod, ["sh", "-c", f"echo {b64} | base64 -d > {path} && echo WRITE_OK"], container=name,
            )
            return bool(out) and "WRITE_OK" in out
        except Exception:
            return False

    def _wait_for_running_pod(self, name: str, ns: str, timeout_s: float = 40.0) -> str | None:
        """Poll for a Running, ready pod of this deployment. Used after a rollout
        this same request just triggered, to write into the pod that actually has
        the freshly (re)attached volume, not whichever pod happened to answer
        list_namespaced_pod a moment before the rollout took effect."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                pods = self.k8s.core.list_namespaced_pod(namespace=ns, label_selector=f"app={name}").items
                pod = next(
                    (p for p in pods if p.status.phase == "Running"
                     and all((cs.ready for cs in (p.status.container_statuses or [])), )),
                    None,
                )
                if pod:
                    return pod.metadata.name
            except Exception:
                pass
            time.sleep(1.5)
        return None

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
        stateful = bool(path) and path in self._writable_doc_path(name, ns)
        if not stateful:
            # For an ordinary document the ConfigMap IS the source of truth (only
            # the dashboard writes there). A stateful doc's ConfigMap entry is just
            # the last thing an operator pasted in: the service itself keeps
            # writing the PVC file directly (e.g. every PUT /bindings), so that
            # snapshot goes stale the moment the service changes anything - read
            # the pod instead, below, which is what is actually running.
            try:
                cm = self.k8s.get_configmap(ns, f"{name}-files")
                content = (cm.get("data") or {}).get(key)
                if content is not None:
                    return {"name": name, "path": path, "content": content, "ephemeral": False}
            except Exception:
                pass
        runtime = self._read_pod_file(name, ns, path)
        # A stateful doc's runtime copy IS its persisted copy (PVC-backed, see
        # _ensure_writable_store): it is never mirrored into the ConfigMap above,
        # so every read takes this branch, and without this check a document that
        # survives every restart would be reported "ephemeral, lost on restart".
        stateful = bool(path) and path in self._writable_doc_path(name, ns)
        return {"name": name, "path": path, "content": runtime, "ephemeral": runtime is not None and not stateful}

    def _stateful_spec(self, name: str, ns: str) -> dict[str, str] | None:
        """{env, path} for this adapter's runtime-written doc, or None. Bridge keyed by
        image basename (_STATEFUL_DOCS) until the contract's `writable` flag reaches the
        dashboard via a rebuilt /contract."""
        try:
            dep = self.k8s.apps.read_namespaced_deployment(name=name, namespace=ns)
            return _STATEFUL_DOCS.get(_image_basename(dep.spec.template.spec.containers[0].image))
        except Exception:
            return None

    def _writable_doc_path(self, name: str, ns: str) -> set[str]:
        """Paths that count as THIS adapter's stateful runtime doc for
        apply_service_file: the image's own default (before the store exists) and
        the already-redirected path inside STORE_DIR (after _ensure_writable_store
        has run once and the env now points there). Both must match, or re-applying
        a document after the first redirect takes the wrong branch below."""
        spec = self._stateful_spec(name, ns)
        if not spec:
            return set()
        fname = spec["path"].rsplit("/", 1)[-1] or "file"
        return {spec["path"], f"{STORE_DIR}/{fname}"}

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
        """Attach the PVC-backed writable store to a stateful adapter (wifi-adapter),
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
        if path in self._writable_doc_path(name, ns):
            self._ensure_writable_store(name, ns)
            # attach_dir_store only mounts the PVC and redirects the env to it; it
            # copies nothing onto the PVC by itself (found live 2026-09-15: a saved
            # document sat in the ConfigMap above while the running pod kept
            # serving its old file, indefinitely, since nothing restarts it either).
            # Restart so a pod with the mount definitely exists, then write the
            # content into it directly.
            self.k8s.restart_deployment(ns, name)
            pod = self._wait_for_running_pod(name, ns)
            written = self._write_pod_file(name, ns, path, content) if pod else False
            return {"status": "applied", "name": name, "config_map": cm_name,
                    "mount": path, "persistent": True, "written": written}

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
        vendor-adapter with no vendor schema). A *_FILE counts as satisfied when ANY
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

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

import io
import ipaddress
import json
import os
import socket
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

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
# Selectorless Service name for the gNB management console external endpoint.
GNB_SVC = "gnb"
# Service port the front-door proxies to for an exposed app, so <name>.<base> works
# regardless of the container's own port.
PUBLISHED_PORT = 80

# Starter-kit files handed to an app developer (README + .env.example + deploy.sh),
# zipped on demand and prefilled with this cluster's registry host (__HOST__). The
# developer only builds and pushes an image; the k8s deploy stays in the dashboard.
_KIT_README = """# KELT edge app — build & deploy

Your container must:
- serve its UI on HTTP `:80` (reachable at `<name>.<base>` when exposed), and/or
- listen on UDP `:5005` for the H264/RTP stream from the UE (MEC apps).

## 1. Build & push (this kit)
1. `./deploy.sh` once — it creates `.env` from `.env.example`.
2. Fill `.env`: `APP_NAME`, `IMAGE_TAG`, `REGISTRY_PASSWORD` (ask the operator;
   `REGISTRY_HOST`/`REGISTRY_USER` are prefilled).
3. `./deploy.sh` again — builds and pushes to the cluster registry, prints the image ref.

The registry is plain HTTP, so your Docker daemon must trust it. In
`/etc/docker/daemon.json`: `{"insecure-registries": ["__HOST__"]}` then restart docker.
Push over LAN/Tailscale (the registry is a NodePort, never the public tunnel).

## 2. Deploy (operator, from the dashboard — no kubectl)
Services -> Edge apps -> Deploy:
- `image` = the ref printed by `deploy.sh`
- `expose` on for an HTTP UI
- MEC video app: tick "attach to MEC network (n6m)", set a fixed IP (10.208.0.200-.207)
  and the UDP ingest port (5005).

The UE then streams to that n6m IP on 5005 over the 5G tunnel.

## Network constraints (MEC apps over the 5G user plane)
UE traffic rides a GTP-U tunnel (UE -> gNB -> UPF -> n6m) that adds ~40 B of header.
The UE-visible MTU is **1400** (advertised by the SMF). To avoid IP fragmentation of
a UDP/RTP stream, size the packets to fit under it:
- UDP payload <= ~1372 B (1400 - 20 IP - 8 UDP)
- H264/RTP: set the packetizer MTU to ~1360, e.g. GStreamer `rtph264pay mtu=1360`
  (use 1200 for extra margin). FFmpeg: `-pkt_size 1200`.
TCP flows are already MSS-clamped to 1360 by the UPF, so they need no tuning. Keep
the bitrate within the link and prefer UDP for real-time video. Full rationale:
docs/architecture/network-topology.md (MTU sizing and GTP-U encapsulation).
"""

_KIT_ENV_EXAMPLE = """# Copy is automatic on first ./deploy.sh. Fill, then run ./deploy.sh again.
APP_NAME=myapp
IMAGE_TAG=dev
REGISTRY_HOST=__HOST__
REGISTRY_USER=kelt
REGISTRY_PASSWORD=
BUILD_CONTEXT=.
DOCKERFILE=Dockerfile
"""

_KIT_DEPLOY_SH = """#!/usr/bin/env bash
# KELT edge app — build & push to the cluster registry. See README.md.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example — fill it, then re-run ./deploy.sh"
  exit 0
fi
set -a; . ./.env; set +a
: "${APP_NAME:?set APP_NAME in .env}"
: "${IMAGE_TAG:?set IMAGE_TAG in .env}"
: "${REGISTRY_HOST:?set REGISTRY_HOST in .env}"
: "${REGISTRY_USER:?set REGISTRY_USER in .env}"
: "${REGISTRY_PASSWORD:?set REGISTRY_PASSWORD in .env}"
IMG="${REGISTRY_HOST}/${APP_NAME}:${IMAGE_TAG}"
echo "${REGISTRY_PASSWORD}" | docker login "${REGISTRY_HOST}" -u "${REGISTRY_USER}" --password-stdin
docker build -t "${IMG}" -f "${DOCKERFILE:-Dockerfile}" "${BUILD_CONTEXT:-.}"
docker push "${IMG}"
echo
echo "Pushed ${IMG}"
echo "Deploy it from the dashboard: Services -> Edge apps -> image = ${IMG}"
"""


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
        # First-level namespaced host: <prefix>-<name>.<base> (free Cloudflare TLS).
        # The front-door's ^<prefix>-(?<app>...) route strips the prefix back to the
        # Service name. See docs/security/external-access.md.
        return f"{settings.external_scheme}://{settings.kelt_prefix}-{name}.{settings.external_base_domain}"

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

    def public_apps(self) -> dict[str, Any]:
        """Names + public URLs of exposed apps, for the pre-auth front-door welcome
        page. Only what the catalogue already shows for core services (name + link);
        never fails the welcome page."""
        out = []
        try:
            for a in self.list_apps():
                if a.get("public_url"):
                    out.append({"name": a["name"], "url": a["public_url"], "ready": a["ready"]})
        except Exception:
            return {"apps": []}
        return {"apps": out}

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
            # MEC attach + requested n6m IP, read from the pod template annotation.
            mec_attached, mec_ip = False, None
            anns = (d.spec.template.metadata.annotations or {})
            try:
                for n in json.loads(anns.get("k8s.v1.cni.cncf.io/networks", "[]")):
                    if isinstance(n, dict) and n.get("name") == "n6m-net":
                        mec_attached = True
                        mec_ip = (n.get("ips") or [None])[0]
            except (ValueError, TypeError):
                pass
            out.append({
                "name": name,
                "image": image,
                # The tag this app was deployed from. `image` is a digest ref once
                # pinned, so this is what the UI shows and update detection re-checks.
                "image_tag": anns.get("kelt.io/image-tag"),
                "namespace": self.ns,
                "replicas": desired,
                "ready_replicas": ready,
                "ready": desired > 0 and ready >= desired,
                "exposed": exposed,
                "public_url": self._public_url(name, exposed),
                "mec_attached": mec_attached,
                "mec_ip": mec_ip,
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
        # Extra UDP ingest ports (e.g. RTP video on the MEC DN) alongside the HTTP
        # port. The UDP traffic arrives on the n6m interface, not via a Service, so
        # these are declared on the container only (informational + documentation).
        ports = [{"containerPort": req.port, "name": "http"}]
        for i, up in enumerate(req.udp_ports or []):
            ports.append({"containerPort": int(up), "protocol": "UDP", "name": f"udp{i}"})
        # The tag the operator picked is resolved to a digest here and kept in an
        # annotation (below) so the UI can still say "v3" and update detection knows
        # which tag to re-check. See docs/architecture/edge-apps.md.
        pinned_image, _pinned_digest = self.resolve_pinned_ref(req.image)
        container: dict[str, Any] = {
            "name": req.name,
            "image": pinned_image,
            # A digest names exactly one image, so there is nothing to re-pull: the
            # node either has it or fetches it once. Always was only ever there to
            # compensate for the mutable tag this now pins.
            "imagePullPolicy": "IfNotPresent",
            "ports": ports,
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

        # MEC attach: a Multus secondary interface on the n6m DN so UEs reach the
        # app via the UPF. Same-namespace NAD reference; `ips` requests the fixed
        # reserved address (whereabouts honors it, like the NF static IPs). Return
        # routes to the UE pools are inherited from the n6m-net NAD itself.
        # Remember the tag the operator deployed from: the image field now carries a
        # digest, which is unreadable in the UI and tells update detection nothing
        # about WHICH tag to re-check.
        pod_meta: dict[str, Any] = {"labels": labels, "annotations": {"kelt.io/image-tag": req.image}}
        if req.attach_mec:
            net: dict[str, Any] = {"name": "n6m-net", "namespace": self.ns, "interface": "n6m"}
            if req.mec_ip:
                net["ips"] = [req.mec_ip if "/" in req.mec_ip else f"{req.mec_ip}/24"]
            pod_meta["annotations"]["k8s.v1.cni.cncf.io/networks"] = json.dumps([net])

        self.k8s.upsert_deployment(self.ns, {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": req.name, "namespace": self.ns, "labels": labels},
            "spec": {
                "replicas": req.replicas,
                "selector": {"matchLabels": {"app": req.name}},
                "template": {"metadata": pod_meta, "spec": pod_spec},
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
            "mec_attached": req.attach_mec,
            "mec_ip": (req.mec_ip or "dynamic") if req.attach_mec else None,
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

    # ── gNB management console (external endpoint via the dynamic apps route) ───
    # The physical gNB / femtocell has a web UI on the RAN management LAN, an IP the
    # operator's browser cannot reach directly. Instead of a per-host tunnel rule,
    # the dashboard registers it as a selectorless Service + Endpoints named "gnb"
    # in the apps namespace; the front-door's dynamic <app>.<base> route then
    # proxies gnb.<base> to it via kube-proxy. No front-door / ansible / cloudflared
    # change. Fully operator-driven: KELT assumes no management subnet exists, so an
    # unset console means no surface. Requires the apps route to be enabled (the
    # surface rides the same <app>.<base> regex). See docs/deployment/physical-ran.md
    # and docs/security/external-access.md.
    @staticmethod
    def _tcp_reachable(host: str, port: int, timeout: float = 1.5) -> bool:
        # Best-effort reachability probe: KELT and the worker reach the femtocell
        # management LAN through the same host NAT path, so a TCP connect from the
        # backend is a faithful indicator that the front-door can reach it too.
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                return True
        except OSError:
            return False

    def gnb_console_status(self) -> dict[str, Any]:
        origin, reachable = None, None
        try:
            ep = self.k8s.core.read_namespaced_endpoints(name=GNB_SVC, namespace=self.ns)
            subsets = ep.subsets or []
            if subsets and subsets[0].addresses and subsets[0].ports:
                host = subsets[0].addresses[0].ip
                port = subsets[0].ports[0].port
                origin = f"{host}:{port}"
                reachable = self._tcp_reachable(host, port)
        except Exception:
            pass
        return {
            "configured": origin is not None,
            "origin": origin,
            "reachable": reachable,
            "url": self._public_url(GNB_SVC, True) if origin else None,
            "namespace": self.ns,
        }

    def set_gnb_console(self, host: str, port: int) -> dict[str, Any]:
        host = (host or "").strip()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            raise AppDeployError(400, f"gNB host must be an IP reachable from the worker, got '{host}'")
        port = int(port)
        if not (1 <= port <= 65535):
            raise AppDeployError(400, f"gNB port out of range: {port}")
        labels = {**_LABELS_BASE, "kelt.io/surface": "gnb-console"}
        # Selectorless Service: kube-proxy honours the manual Endpoints below, so the
        # ClusterIP:80 the front-door proxies to is DNAT'd to the appliance IP:port.
        self.k8s.upsert_service(self.ns, {
            "apiVersion": "v1", "kind": "Service",
            "metadata": {"name": GNB_SVC, "namespace": self.ns, "labels": labels},
            "spec": {"ports": [{"name": "http", "port": PUBLISHED_PORT, "targetPort": port}]},
        })
        endpoints = {
            "apiVersion": "v1", "kind": "Endpoints",
            "metadata": {"name": GNB_SVC, "namespace": self.ns, "labels": labels},
            "subsets": [{"addresses": [{"ip": host}], "ports": [{"name": "http", "port": port}]}],
        }
        try:
            self.k8s.core.read_namespaced_endpoints(name=GNB_SVC, namespace=self.ns)
            self.k8s.core.replace_namespaced_endpoints(name=GNB_SVC, namespace=self.ns, body=endpoints)
        except Exception:
            self.k8s.core.create_namespaced_endpoints(namespace=self.ns, body=endpoints)
        return self.gnb_console_status()

    def clear_gnb_console(self) -> dict[str, Any]:
        self.k8s.delete_service(self.ns, GNB_SVC)
        try:
            self.k8s.core.delete_namespaced_endpoints(name=GNB_SVC, namespace=self.ns)
        except Exception:
            pass
        return {"configured": False, "origin": None, "url": None, "namespace": self.ns}

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

    def starter_kit_zip(self) -> bytes:
        """A zip (README + .env.example + deploy.sh) the operator hands to an app
        developer, prefilled with this cluster's registry host. The developer only
        builds and pushes; the deploy happens in the dashboard."""
        host = settings.apps_registry_host or "<registry-host>"
        files = {
            "README.md": (_KIT_README.replace("__HOST__", host), 0o644),
            ".env.example": (_KIT_ENV_EXAMPLE.replace("__HOST__", host), 0o644),
            "deploy.sh": (_KIT_DEPLOY_SH.replace("__HOST__", host), 0o755),
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, (content, mode) in files.items():
                info = zipfile.ZipInfo(name)
                info.external_attr = mode << 16  # preserve the +x bit on deploy.sh
                z.writestr(info, content)
        return buf.getvalue()

    # ── Registry catalog (admin only) ─────────────────────────────────────────
    def registry_images(self) -> dict[str, Any]:
        """List repos+tags in the local registry so the operator can deploy a
        pushed image without retyping the tag. Queries the registry v2 API over
        the same host/basic-auth used for push. Degrades to reachable:false when
        the registry is down or not configured (no hard failure)."""
        host = settings.apps_registry_host
        if not host:
            return {"reachable": False, "host": "", "images": []}
        base = f"http://{host}"
        auth = (settings.apps_registry_username, settings.apps_registry_password) \
            if settings.apps_registry_username else None
        manifest_accept = ("application/vnd.docker.distribution.manifest.v2+json, "
                           "application/vnd.oci.image.manifest.v1+json")

        def _created(c: httpx.Client, repo: str, tag: str) -> str | None:
            # tag -> manifest -> config blob -> .created (image build time). Single-arch
            # manifest only (operator builds); a multi-arch list has no .config -> None.
            try:
                m = c.get(f"{base}/v2/{repo}/manifests/{tag}", auth=auth,
                          headers={"Accept": manifest_accept})
                cfg = ((m.json() if m.status_code == 200 else {}) or {}).get("config", {}).get("digest")
                if not cfg:
                    return None
                b = c.get(f"{base}/v2/{repo}/blobs/{cfg}", auth=auth)
                return (b.json().get("created") if b.status_code == 200 else None)
            except (httpx.HTTPError, ValueError):
                return None

        try:
            with httpx.Client(timeout=5.0) as c:
                cat = c.get(f"{base}/v2/_catalog", auth=auth)
                cat.raise_for_status()
                repos = cat.json().get("repositories", []) or []
                images = []
                for repo in repos:
                    tags: list[dict[str, Any]] = []
                    try:
                        t = c.get(f"{base}/v2/{repo}/tags/list", auth=auth)
                        if t.status_code == 200:
                            for tag in (t.json().get("tags") or []):
                                tags.append({"tag": tag, "created": _created(c, repo, tag)})
                    except httpx.HTTPError:
                        pass
                    # Newest first (ISO timestamps sort chronologically); undated last.
                    tags.sort(key=lambda x: x["created"] or "", reverse=True)
                    images.append({"repo": repo, "tags": tags})
            return {"reachable": True, "host": host, "images": images}
        except httpx.HTTPError as exc:
            return {"reachable": False, "host": host, "images": [], "error": str(exc)[:200]}

    # ── Update detection + rollout (admin only) ──────────────────────────────
    # An operator iterates by re-pushing an image (same or new tag). With
    # imagePullPolicy: Always a redeploy/restart pulls the newest digest for the tag.
    # check_updates() flags apps whose registry digest differs from the running pod's
    # digest (a newer push), so the UI can suggest an update; restart_app() does the
    # one-click rollout that pulls it. See docs/architecture/edge-apps.md.
    def _registry_manifest_digest(self, repo: str, tag: str) -> str | None:
        host = settings.apps_registry_host
        if not host:
            return None
        auth = (settings.apps_registry_username, settings.apps_registry_password) \
            if settings.apps_registry_username else None
        accept = ", ".join([
            "application/vnd.docker.distribution.manifest.v2+json",
            "application/vnd.oci.image.manifest.v1+json",
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.oci.image.index.v1+json",
        ])
        try:
            with httpx.Client(timeout=4.0) as c:
                r = c.head(f"http://{host}/v2/{repo}/manifests/{tag}", auth=auth,
                           headers={"Accept": accept})
                if r.status_code == 200:
                    return r.headers.get("Docker-Content-Digest")
        except httpx.HTTPError:
            pass
        return None

    def resolve_pinned_ref(self, image: str) -> tuple[str, str | None]:
        """Turn `host/repo:tag` into `host/repo@sha256:...` by asking the registry
        what that tag points at RIGHT NOW.

        A tag in the local registry is mutable by design: an operator iterates by
        re-pushing the same one. Deploying the tag therefore left the running image
        undefined, decided by whatever the kubelet happened to cache and re-resolved
        behind our back on any restart or reschedule. Pinning the digest makes the
        deploy mean exactly one image: what the operator chose at that moment.
        Re-resolving is then an explicit action, not a side effect of a restart.

        Returns (ref_to_deploy, resolved_digest). Falls back to the original ref when
        the image is not ours or the registry cannot answer, so a public image or an
        unreachable registry still deploys (just unpinned, as before)."""
        host = settings.apps_registry_host or ""
        if not host or not image.startswith(host + "/") or "@sha256:" in image:
            return image, None
        ref = image[len(host) + 1:]
        repo, sep, tag = ref.rpartition(":")
        if not sep:
            repo, tag = ref, "latest"
        digest = self._registry_manifest_digest(repo, tag)
        if not digest:
            return image, None
        return f"{host}/{repo}@{digest}", digest

    def _running_digest(self, app: str) -> str | None:
        try:
            pods = self.k8s.core.list_namespaced_pod(
                namespace=self.ns, label_selector=f"app={app}").items
        except Exception:
            return None
        # Only a Running pod that is NOT being deleted: during a rollout a lingering
        # old replica would otherwise report the previous image's digest and produce
        # a false "update available". If none is clean yet, return None (don't compare).
        for p in pods:
            if (p.status.phase or "") != "Running" or p.metadata.deletion_timestamp:
                continue
            for cs in (p.status.container_statuses or []):
                iid = cs.image_id or ""
                if "@sha256:" in iid:
                    return "sha256:" + iid.split("@sha256:", 1)[1]
        return None

    def check_updates(self) -> dict[str, Any]:
        """Per app: is the registry digest for its tag newer than the running pod's?
        Best-effort (a registry/pod read failure just yields update_available=false)."""
        host = settings.apps_registry_host or ""
        apps: dict[str, bool] = {}
        for app in self.list_apps():
            name, image = app["name"], (app.get("image") or "")
            apps[name] = False
            # The deployed image is a digest, so the tag to re-check comes from the
            # annotation written at deploy time. Legacy apps deployed before pinning
            # still carry a tag in the image field: fall back to parsing it.
            source = app.get("image_tag") or image
            if not host or not source.startswith(host + "/"):
                continue  # public/other-registry image: nothing to compare against
            ref = source[len(host) + 1:]
            repo, sep, tag = ref.rpartition(":")
            if not sep or "@sha256" in ref:
                continue  # pinned with no recorded tag: nothing meaningful to compare
            reg = self._registry_manifest_digest(repo, tag)
            run = self._running_digest(name)
            apps[name] = bool(reg and run and reg != run)
        return {"checked": True, "apps": apps}

    def set_app_image(self, name: str, image: str) -> dict[str, Any]:
        """Retarget a deployed app to a registry image (a chosen tag), preserving its
        port/expose/MEC/env. The tag is resolved to a digest and pinned, so this is
        also the "re-pull" path: re-running it against the same tag picks up a
        re-push, because the tag now resolves to a different digest. The restart
        annotation covers the case where the digest is unchanged and the operator
        still wants the pod recreated. The operator picks the version from the
        date-ordered registry tag list."""
        _validate_name(name)
        _validate_image(image)
        pinned_image, _ = self.resolve_pinned_ref(image)
        self.k8s.apps.patch_namespaced_deployment(
            name=name, namespace=self.ns,
            body={"spec": {"template": {
                "metadata": {"annotations": {
                    "kelt.io/restartedAt": datetime.now(timezone.utc).isoformat(),
                    "kelt.io/image-tag": image}},
                "spec": {"containers": [
                    {"name": name, "image": pinned_image, "imagePullPolicy": "IfNotPresent"}]},
            }}},
        )
        return {"status": "updating", "name": name, "image": image, "namespace": self.ns}

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

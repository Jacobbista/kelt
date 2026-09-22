from typing import Any

from pydantic import BaseModel, Field


class PodSummary(BaseModel):
    name: str
    namespace: str
    phase: str
    restarts: int
    node: str | None = None
    pod_ip: str | None = None
    start_time: str | None = None
    deployment: str | None = None
    containers: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    # Container truth, not just the pod phase: a crashlooping pod stays phase=Running,
    # so `ready` and `waiting_reason` are what actually distinguishes healthy from stuck.
    # `image` is the image the pod really runs, which can differ from the deployment spec
    # during a failed rollout (old pod still serving while the new one crashes).
    ready: bool = False
    waiting_reason: str | None = None
    image: str | None = None


class NodeSummary(BaseModel):
    name: str
    status: str
    roles: list[str] = Field(default_factory=list)
    ip: str | None = None
    kubelet_version: str | None = None


class ClusterStats(BaseModel):
    total_pods: int
    running: int
    pending: int
    failed: int


class ClusterSummary(BaseModel):
    nodes: list[NodeSummary]
    stats: ClusterStats


class NfInstance(BaseModel):
    nf_type: str
    category: str
    name: str
    phase: str
    restarts: int
    node: str | None = None
    pod_ip: str | None = None
    start_time: str | None = None
    deployment: str | None = None
    containers: list[str] = Field(default_factory=list)


class NfStatusResponse(BaseModel):
    control_plane: list[NfInstance] = Field(default_factory=list)
    user_plane: list[NfInstance] = Field(default_factory=list)
    data: list[NfInstance] = Field(default_factory=list)
    other: list[NfInstance] = Field(default_factory=list)


class TopologyNode(BaseModel):
    id: str
    type: str
    label: str
    data: dict[str, Any] = Field(default_factory=dict)


class TopologyEdge(BaseModel):
    id: str
    source: str
    target: str
    label: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class TopologyResponse(BaseModel):
    nodes: list[TopologyNode]
    edges: list[TopologyEdge]


class RestartRequest(BaseModel):
    namespace: str = "5g"


class ScaleControllerRequest(BaseModel):
    namespace: str = "5g"
    kind: str
    name: str
    replicas: int = Field(ge=0)


class ConfigMapPayload(BaseModel):
    data: dict[str, str]
    restart_deployments: list[str] = Field(default_factory=list)


class LogLevelPayload(BaseModel):
    level: str  # debug, info, warning, error


# ── Front-door co-branding (welcome page) ────────────────────────────────────
class BrandRequest(BaseModel):
    # Org co-brand for the front-door welcome page (written to the frontdoor-brand
    # ConfigMap / brand.json). All optional; empty clears that field (KELT-only /
    # default tagline). org_logo is a data-URI (uploaded image) or a URL.
    org_name: str = ""
    org_logo: str = ""
    accent: str = ""
    tagline: str = ""
    logo_bg: str = ""  # backdrop behind the logo for contrast: "" | "light" | "dark"


# ── Northbound (positioning/CAMARA) service-management console ───────────────
class AdapterUpgradeRequest(BaseModel):
    # Targeted, in-place upgrade of a catalog adapter: patch only the deployment
    # image to this `image:tag`; the envFrom config is preserved.
    image: str


class DeployEnvVar(BaseModel):
    name: str
    value: str
    sensitive: bool = False  # sensitive vars go into a Secret, not the Deployment env


class DeployImageRequest(BaseModel):
    # Deploy a custom adapter image into the positioning namespace. v0.6.0: the
    # adapter self-registers with the engine (the deploy injects the registration
    # env), so there is no manual register step. What the adapter IS (its family,
    # its source) is declared by the image and by ADAPTER_CAPABILITIES afterwards,
    # never chosen here (ADAPTER_KIND was removed upstream in 0.17.1).
    name: str
    image: str
    port: int = Field(default=8080, ge=1, le=65535)
    env: list[DeployEnvVar] = Field(default_factory=list)
    image_pull_secret: str | None = None  # name of a pre-created dockerconfigjson Secret


class WorkloadDeployRequest(BaseModel):
    # Generic "deploy any image as a scheduled workload" (the Custom workload card).
    # Lands in an allow-listed namespace; not registered as a positioning adapter.
    name: str
    image: str
    port: int = Field(default=8080, ge=1, le=65535)
    env: list[DeployEnvVar] = Field(default_factory=list)
    image_pull_secret: str | None = None
    namespace: str = "mec"


class AppDeployRequest(BaseModel):
    # Edge apps platform (phase 12): deploy an operator's own image as a pod in the
    # mec namespace. When `expose` is true the Service is published on port 80
    # (-> container `port`) so the front-door reaches it at <name>.<base> without
    # knowing the container port. Readiness is a TCP probe (arbitrary images need
    # not expose /health). See docs/architecture/edge-apps.md.
    name: str
    image: str
    port: int = Field(default=80, ge=1, le=65535)
    replicas: int = Field(default=1, ge=0, le=10)
    env: list[DeployEnvVar] = Field(default_factory=list)
    image_pull_secret: str | None = None
    expose: bool = True
    # MEC data network (n6m): attach a secondary interface so UEs reach the app over
    # the 5G user plane (UPF -> n6m). `mec_ip` requests a fixed address from the
    # reserved band (whereabouts honors `ips`); empty = dynamic pool IP. `udp_ports`
    # are extra container UDP ports (e.g. an RTP video ingest) that arrive on n6m,
    # not via the front-door. See docs/architecture/edge-apps.md and 5g-interfaces.md.
    attach_mec: bool = False
    mec_ip: str | None = None
    udp_ports: list[int] = Field(default_factory=list)


class ServiceConfigRequest(BaseModel):
    # Guided-setup apply: a flat {VAR: value} map. The backend routes each var by
    # the service contract's `sensitive` flag (Secret vs ConfigMap) and rejects any
    # name not in the contract. A value of null UNSETS the var (deletes the key),
    # e.g. clearing an inline override so a file-backed value takes effect.
    values: dict[str, str | None] = Field(default_factory=dict)


class ServiceBindingRequest(BaseModel):
    # One runtime choice on an adapter (e.g. motion_model), from the set its
    # /contract declares. The backend checks the set before calling the pod.
    key: str
    value: str


class ServiceFileRequest(BaseModel):
    # A file-backed config field (a contract *_FILE path): the document content is
    # stored in the service's <name>-files ConfigMap and mounted at `path`.
    path: str
    content: str = ""


class AssetStoreRequest(BaseModel):
    # Full Asset Identity Map store (asset.schema.json v3: an asset carries N
    # capabilities the gateway fuses). The gateway PUT /assets replaces the store, so
    # the dashboard sends the complete set (load-all, edit, save-all); the gateway
    # validates each entry against the upstream schema. The editor is admin-only and the
    # caller's Bearer (dashboard-admin, composite with camara-location-read) is forwarded
    # to the gateway. The store is a pass-through: shape is validated upstream, not here.
    version: int = 3
    assets: list[dict[str, Any]] = Field(default_factory=list)

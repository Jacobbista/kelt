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
    # env), so there is no manual register step. `kind` sets ADAPTER_KIND (the
    # positioning modality the engine/demo show, e.g. wifi/uwb/mock).
    name: str
    image: str
    port: int = Field(default=8080, ge=1, le=65535)
    env: list[DeployEnvVar] = Field(default_factory=list)
    image_pull_secret: str | None = None  # name of a pre-created dockerconfigjson Secret
    kind: str = ""  # ADAPTER_KIND override; empty keeps the image's own default


class FusionConfigPayload(BaseModel):
    strategy: str | None = None
    compare: str | None = None
    device_map: str | None = None


class CoreImageRequest(BaseModel):
    # Retarget a managed northbound deployment (gateway/engine/demo) to a new image.
    image: str


class WorkloadDeployRequest(BaseModel):
    # Generic "deploy any image as a scheduled workload" (the Custom workload card).
    # Lands in an allow-listed namespace; not registered as a positioning adapter.
    name: str
    image: str
    port: int = Field(default=8080, ge=1, le=65535)
    env: list[DeployEnvVar] = Field(default_factory=list)
    image_pull_secret: str | None = None
    namespace: str = "mec"


class ServiceConfigRequest(BaseModel):
    # Guided-setup apply: a flat {VAR: value} map. The backend routes each var by
    # the service contract's `sensitive` flag (Secret vs ConfigMap) and rejects any
    # name not in the contract. A value of null UNSETS the var (deletes the key),
    # e.g. clearing an inline override so a file-backed value takes effect.
    values: dict[str, str | None] = Field(default_factory=dict)


class ServiceFileRequest(BaseModel):
    # A file-backed config field (a contract *_FILE path): the document content is
    # stored in the service's <name>-files ConfigMap and mounted at `path`.
    path: str
    content: str = ""

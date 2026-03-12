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

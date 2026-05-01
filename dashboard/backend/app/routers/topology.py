from fastapi import APIRouter, Depends

from app.config import settings
from app.models import TopologyEdge, TopologyNode, TopologyResponse
from app.services.k8s_service import K8sService, get_k8s_service
from app.services.ovs_service import OvsService

router = APIRouter(prefix="/api/v1", tags=["topology"])

NAD_TO_BRIDGE = {
    "n1-net": "br-n1",
    "n2-net": "br-n2",
    "n2-cell-1": "br-n2-cell-1",
    "n3-net": "br-n3",
    "n3-cell-1": "br-n3-cell-1",
    "n4-net": "br-n4",
    "n6c-net": "br-n6c",
    "n6e-net": "br-n6e",
    "n6m-net": "br-n6m",
}


def infer_nf_type(name: str, labels: dict[str, str]) -> str:
    app = labels.get("app", "")
    value = app or name
    for kind in ["amf", "smf", "upf", "nrf", "udm", "udr", "ausf", "pcf", "nssf", "bsf", "gnb", "ue"]:
        if kind in value:
            return kind
    return "pod"


@router.get("/topology", response_model=TopologyResponse)
def get_topology(
    namespace: str = settings.default_namespace,
    k8s: K8sService = Depends(get_k8s_service),
) -> TopologyResponse:
    ovs = OvsService()
    bridges = ovs.list_bridges()
    nodes: dict[str, TopologyNode] = {}
    edges: dict[str, TopologyEdge] = {}

    for bridge in bridges:
        node_id = f"bridge:{bridge}"
        nodes[node_id] = TopologyNode(
            id=node_id,
            type="bridge",
            label=bridge,
            data={"bridge": bridge, "ports": ovs.list_bridge_ports(bridge)},
        )

    for pod in k8s.list_topology_data(namespace):
        pod_id = f"pod:{pod['name']}"
        labels = pod.get("labels", {})
        nodes[pod_id] = TopologyNode(
            id=pod_id,
            type=infer_nf_type(pod["name"], labels),
            label=labels.get("app", pod["name"]),
            data={
                "pod": pod["name"],
                "namespace": pod["namespace"],
                "phase": pod["phase"],
                "node": pod.get("node"),
            },
        )

        for iface in pod.get("networks", []):
            net_name = iface.get("name", "").split("/")[-1]
            bridge = NAD_TO_BRIDGE.get(net_name)
            if not bridge:
                continue
            bridge_id = f"bridge:{bridge}"
            edge_id = f"{pod_id}->{bridge_id}:{iface.get('interface', 'net')}"
            edges[edge_id] = TopologyEdge(
                id=edge_id,
                source=pod_id,
                target=bridge_id,
                label=net_name,
                data={
                    "interface": iface.get("interface"),
                    "ips": iface.get("ips", []),
                    "mac": iface.get("mac"),
                    "mtu": iface.get("mtu"),
                },
            )

    return TopologyResponse(nodes=list(nodes.values()), edges=list(edges.values()))


@router.get("/ovs/bridges/{bridge}/flows")
def get_bridge_flows(bridge: str) -> dict[str, str]:
    ovs = OvsService()
    return {"bridge": bridge, "flows": ovs.dump_flows(bridge)}

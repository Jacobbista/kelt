"""Decide whether a flow passes the cluster's NetworkPolicies, without sending it.

Implements the Kubernetes NetworkPolicy semantics the phase 13 policies use:
pod selectors by `matchLabels`, namespace selectors by namespace labels,
`ipBlock` with `except`, ports. A pod no policy selects accepts (or sends)
everything; once a policy selects it, only what some policy allows gets through.

A source or destination is either a pod (`namespace` + `labels`) or an address
(`namespace` None + `ip`). A Service destination is its selector's labels: the
check uses workload identity, so `ipBlock` rules match only address endpoints.
See docs/architecture/namespaces.md#network-policies.
"""
import ipaddress
from typing import Any

Policy = dict[str, Any]
Endpoint = dict[str, Any]


def _expression(expr: dict, labels: dict) -> bool:
    key, op, values = expr.get("key"), expr.get("operator"), expr.get("values") or []
    if op == "In":
        return key in labels and labels[key] in values
    if op == "NotIn":
        return key not in labels or labels[key] not in values
    if op == "Exists":
        return key in labels
    if op == "DoesNotExist":
        return key not in labels
    return False  # unknown operator: select nothing rather than everything


def _selects(selector: dict | None, labels: dict) -> bool:
    selector = selector or {}
    wanted = selector.get("matchLabels") or {}
    return (all(labels.get(k) == v for k, v in wanted.items())
            and all(_expression(e, labels) for e in selector.get("matchExpressions") or []))


def _in_block(ip: str | None, block: dict) -> bool:
    if not ip:
        return False
    addr = ipaddress.ip_address(ip)
    if addr not in ipaddress.ip_network(block["cidr"], strict=False):
        return False
    return not any(addr in ipaddress.ip_network(e, strict=False) for e in block.get("except") or [])


def _peer_matches(peer: dict, other: Endpoint, own_ns: str, namespaces: dict) -> bool:
    """Does `other` match one `from`/`to` peer of a policy living in `own_ns`?"""
    if "ipBlock" in peer:
        return other.get("namespace") is None and _in_block(other.get("ip"), peer["ipBlock"])
    if other.get("namespace") is None:
        return False
    if "namespaceSelector" in peer:
        ns_labels = namespaces.get(other["namespace"], {"kubernetes.io/metadata.name": other["namespace"]})
        if not _selects(peer["namespaceSelector"], ns_labels):
            return False
        return _selects(peer.get("podSelector"), other.get("labels") or {})
    if "podSelector" in peer:
        return other["namespace"] == own_ns and _selects(peer["podSelector"], other.get("labels") or {})
    return False


def _port_matches(ports: list | None, port: int | None, protocol: str) -> bool:
    if not ports:
        return True
    for p in ports:
        if (p.get("protocol") or "TCP") != protocol:
            continue
        wanted = p.get("port")
        if wanted is None or isinstance(wanted, str) or port is None:
            return True  # a named port cannot be resolved here: treated as a match
        if wanted == port or (p.get("endPort") is not None and wanted <= port <= p["endPort"]):
            return True
    return False


def _name(p: Policy) -> str:
    return f"{p['metadata']['namespace']}/{p['metadata']['name']}"


def _policy_types(spec: dict) -> list[str]:
    # Unset policyTypes: Ingress always, Egress when the policy has egress rules.
    if spec.get("policyTypes"):
        return spec["policyTypes"]
    return ["Ingress"] + (["Egress"] if spec.get("egress") else [])


def _selecting(policies: list[Policy], namespace: str, labels: dict, kind: str) -> list[Policy]:
    return [p for p in policies
            if p["metadata"]["namespace"] == namespace
            and kind in _policy_types(p["spec"])
            and _selects(p["spec"].get("podSelector"), labels)]


def _deny_name(selecting: list[Policy], namespace: str, kind: str) -> str:
    default = f"default-deny-{kind.lower()}"
    names = [p["metadata"]["name"] for p in selecting]
    return f"{namespace}/{default}" if default in names else _name(selecting[0])


def _describe(e: Endpoint) -> str:
    if e.get("namespace") is None:
        return e.get("ip") or "an outside address"
    app = (e.get("labels") or {}).get("app")
    return f"{e['namespace']}/{app}" if app else e["namespace"]


def _egress(policies, namespaces, src: Endpoint, dst: Endpoint) -> dict:
    if src.get("namespace") is None:
        return {"side": "egress", "result": "na", "text": "An address outside the pod network has no egress policy.", "policy": None}
    selecting = _selecting(policies, src["namespace"], src.get("labels") or {}, "Egress")
    if not selecting:
        return {"side": "egress", "result": "na", "text": f"Leaving {src['namespace']}: no egress limit.", "policy": None}
    protocol = dst.get("protocol") or "TCP"
    for p in selecting:
        for rule in p["spec"].get("egress") or []:
            peers = rule.get("to")
            if (not peers or any(_peer_matches(peer, dst, src["namespace"], namespaces) for peer in peers)) \
                    and _port_matches(rule.get("ports"), dst.get("port"), protocol):
                return {"side": "egress", "result": "ok",
                        "text": f"Leaving {src['namespace']}: {_describe(dst)} is an allowed destination.", "policy": _name(p)}
    return {"side": "egress", "result": "no",
            "text": f"Leaving {src['namespace']}: no policy lets it reach {_describe(dst)}.",
            "policy": _deny_name(selecting, src["namespace"], "Egress")}


def _ingress(policies, namespaces, src: Endpoint, dst: Endpoint) -> dict:
    if dst.get("namespace") is None:
        return {"side": "ingress", "result": "na", "text": "An address outside the cluster: no ingress policy to check.", "policy": None}
    ns = dst["namespace"]
    selecting = _selecting(policies, ns, dst.get("labels") or {}, "Ingress")
    if not selecting:
        return {"side": "ingress", "result": "ok", "text": f"Entering {ns}: no policy selects it, so it accepts everything.", "policy": None}
    protocol = dst.get("protocol") or "TCP"
    for p in selecting:
        for rule in p["spec"].get("ingress") or []:
            peers = rule.get("from")
            if (not peers or any(_peer_matches(peer, src, ns, namespaces) for peer in peers)) \
                    and _port_matches(rule.get("ports"), dst.get("port"), protocol):
                return {"side": "ingress", "result": "ok",
                        "text": f"Entering {ns}: {_describe(src)} may reach {_describe(dst)}.", "policy": _name(p)}
    return {"side": "ingress", "result": "no",
            "text": f"Entering {ns}: no policy lets {_describe(src)} reach {_describe(dst)}.",
            "policy": _deny_name(selecting, ns, "Ingress")}


def evaluate(policies: list[Policy], namespaces: dict[str, dict], src: Endpoint, dst: Endpoint) -> dict:
    """Verdict ("passes" or "blocked") and the egress and ingress steps behind it."""
    egress = _egress(policies, namespaces, src, dst)
    if egress["result"] == "no":
        ingress = {"side": "ingress", "result": "na", "text": "Not reached.", "policy": None}
    else:
        ingress = _ingress(policies, namespaces, src, dst)
    blocked = "no" in (egress["result"], ingress["result"])
    return {"verdict": "blocked" if blocked else "passes", "steps": [egress, ingress]}

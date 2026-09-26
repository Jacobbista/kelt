"""What may talk to what, and what was stopped: the plane filter on the worker
(nftables inet kelt_planes, phase 04) and the NetworkPolicies (phase 13).

Read-only. Counters come from Prometheus (kelt_plane_crossing_packets_total, one
series per filter rule), samples of stopped packets from the worker kernel log,
policies from the Kubernetes API. The clients are passed in, so the logic can be
tested without a cluster. See docs/architecture/plane-isolation.md and
docs/architecture/namespaces.md#network-policies.
"""
import logging
import re
from typing import Any, Callable

from app.services.netpol_eval import evaluate

log = logging.getLogger(__name__)

MANAGED_BY = "app.kubernetes.io/managed-by=kelt-network-policies"
_WINDOW = re.compile(r"^(\d+)([mhd])$")
_MAX_WINDOW_MINUTES = 7 * 24 * 60
# Newest matching kernel-log lines read per request: a flood of drops can log far
# more in 24 h, and the latest few per rule are all the page shows.
SAMPLE_LINES = 2000
_SAMPLE = re.compile(r"^(\S+) \S+ kernel: KELT-PLANES (\w+)(?: ([^:\s]+))?: (.*)$")
_FIELD = re.compile(r"\b(IN|OUT|SRC|DST|PROTO|SPT|DPT)=(\S*)")


def parse_rule(rule: str) -> dict:
    """A filter rule label, as ovs-setup.sh writes it after "allowed: " / "not allowed: "."""
    m = re.match(r"^(\S+) -> (\S+)( replies)?$", rule)
    if not m:
        src = rule[len("from "):] if rule.startswith("from br-") else None
        return {"kind": "outside", "from": src, "to": None}
    a, b, replies = m.groups()
    if replies:
        return {"kind": "replies", "from": a, "to": b}
    if a.startswith("br-") and b.startswith("br-"):
        return {"kind": "pair", "from": a, "to": b}
    return {"kind": "egress", "from": a, "to": b}


def plane_name(bridge: str) -> str:
    """br-ran -> RAN, br-n6c -> N6c, br-n2-cell-1 -> N2 cell 1."""
    name = bridge[len("br-"):]
    if name == "ran":
        return "RAN"
    m = re.match(r"^n(\d+)([a-z]*)(?:-cell-(\d+))?$", name)
    if not m:
        return name
    return f"N{m.group(1)}{m.group(2)}" + (f" cell {m.group(3)}" if m.group(3) else "")


def _plane_order(bridge: str) -> tuple:
    if bridge == "br-ran":
        return (-1, "", 0)
    m = re.match(r"^br-n(\d+)([a-z]*)(?:-cell-(\d+))?$", bridge)
    return (int(m.group(1)), m.group(2), int(m.group(3) or 0)) if m else (99, bridge, 0)


def _window_minutes(window: str) -> int:
    m = _WINDOW.match(window or "")
    if not m:
        raise ValueError("window must look like 30m, 24h or 7d")
    minutes = int(m.group(1)) * {"m": 1, "h": 60, "d": 1440}[m.group(2)]
    if not 0 < minutes <= _MAX_WINDOW_MINUTES:
        raise ValueError("window must be between 1m and 7d")
    return minutes


def _ssh_run(cmd: list[str]) -> tuple[int, str, str]:
    import subprocess

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    return proc.returncode, proc.stdout, proc.stderr


class IsolationService:
    def __init__(self, prometheus=None, run: Callable | None = None, list_policies: Callable | None = None,
                 namespace_roles: dict | None = None, k8s=None) -> None:
        self._prometheus = prometheus
        self._run = run or _ssh_run
        self._list_policies = list_policies
        self._roles = namespace_roles
        self._k8s = k8s

    # Clients are created on first use: they need the backend settings and a
    # kubeconfig, which the unit tests do not have.
    @property
    def prometheus(self):
        if self._prometheus is None:
            from app.services.prometheus_service import PrometheusService
            self._prometheus = PrometheusService()
        return self._prometheus

    @property
    def k8s(self):
        if self._k8s is None:
            from app.services.k8s_service import K8sService
            self._k8s = K8sService()
        return self._k8s

    def _worker_host(self) -> str:
        if self._run is not _ssh_run:
            return "worker"
        from app.config import settings
        return settings.worker_ssh_host

    def _roles_map(self) -> dict:
        if self._roles is None:
            from app.services.network_plan import plan_value
            try:
                self._roles = {plan_value(var): text for var, text in (plan_value("namespace_roles") or {}).items()}
            except KeyError:
                self._roles = {}
        return self._roles

    def _policies(self) -> list[dict]:
        if self._list_policies is not None:
            return self._list_policies()
        from kubernetes import client
        api = client.NetworkingV1Api(self.k8s.core.api_client)
        items = api.list_network_policy_for_all_namespaces(label_selector=MANAGED_BY).items
        return [api.api_client.sanitize_for_serialization(p) for p in items]

    # ── Plane filter ───────────────────────────────────────────────────────

    async def planes(self, window: str = "24h") -> dict[str, Any]:
        _window_minutes(window)
        out: dict[str, Any] = {"window": window, "available": True, "reason": None, "mode": None,
                               "planes": [], "pairs": [], "outside": [], "blocked_total": 0}
        try:
            data = await self.prometheus.instant_query(
                f"sum by (rule, verdict) (increase(kelt_plane_crossing_packets_total[{window}]))")
            enforced = await self.prometheus.instant_query("max(kelt_plane_filter_enforced)")
            # The rules the exporter publishes now: a removed bridge's rules still
            # have an increase over the window, but are gone from here.
            current = await self.prometheus.instant_query("count by (rule, verdict) (kelt_plane_crossing_packets_total)")
        except Exception as exc:  # Prometheus down or unreachable: the page says so
            log.warning("isolation: Prometheus query failed: %s", exc)
            out.update(available=False, reason=f"Prometheus unreachable: {exc}")
            return out

        series = [(r["metric"].get("rule", ""), r["metric"].get("verdict", ""), round(float(r["value"][1])))
                  for r in data.get("result", [])]
        if not series:
            # Prometheus answers but has no counter: the exporter or the filter is
            # missing. That is not "nothing blocked".
            out.update(available=False, reason="no plane filter counters in Prometheus (plane-metrics in the "
                                               "ds-net-setup DaemonSet, node-exporter textfile collector)")
            return out
        if enforced.get("result"):
            out["mode"] = "enforce" if float(enforced["result"][0]["value"][1]) >= 1 else "observe"

        # The filter writes the not-allowed rules for the bridges present on the
        # worker; the allowed rules are fixed (br-ran even when the physical RAN is
        # off). Planes are the bridges that have not-allowed rules now.
        bridges: set[str] = set()
        for r in current.get("result", []):
            rule, verdict = r["metric"].get("rule", ""), r["metric"].get("verdict", "")
            p = parse_rule(rule)
            if verdict == "not_allowed" and p["kind"] == "pair":
                bridges.update([p["from"], p["to"]])
            elif verdict == "not_allowed" and p["from"]:
                bridges.add(p["from"])

        replies = 0
        for rule, verdict, packets in series:
            if verdict == "not_allowed":
                out["blocked_total"] += packets
            p = parse_rule(rule)
            if p["kind"] == "pair":
                if p["from"] in bridges and p["to"] in bridges:
                    out["pairs"].append({"from": p["from"], "to": p["to"], "rule": rule, "verdict": verdict, "packets": packets})
            elif p["kind"] == "egress":
                if p["from"] in bridges:
                    out["pairs"].append({"from": p["from"], "to": "Internet", "rule": rule, "verdict": verdict, "packets": packets})
            elif p["kind"] == "replies":
                replies += packets
            else:
                out["outside"].append({"rule": rule, "verdict": verdict, "packets": packets})
        for pair in out["pairs"]:
            pair["from"] = plane_name(pair["from"])
            if pair["to"] != "Internet":
                pair["to"] = plane_name(pair["to"])
            else:
                pair["replies"] = replies
        out["planes"] = [plane_name(b) for b in sorted(bridges, key=_plane_order)]
        out["outside"].sort(key=lambda o: -o["packets"])
        return out

    def samples(self, limit: int = 5) -> dict[str, Any]:
        """The latest logged packets per not-allowed rule, newest first (last 24 h)."""
        cmd = ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "LogLevel=ERROR", "-o", "BatchMode=yes",
               self._worker_host(), f"sudo journalctl -k --since -24h -n {SAMPLE_LINES} -o short-iso --no-pager --grep KELT-PLANES"]
        try:
            rc, stdout, stderr = self._run(cmd)
        except Exception as exc:
            return {"available": False, "reason": f"Cannot read the worker kernel log: {exc}", "samples": []}
        # journalctl --grep exits 1 when nothing matches, and so do sudo and ssh
        # errors: only an exit 1 with nothing on stderr is an empty journal.
        if rc not in (0, 1) or (rc == 1 and stderr.strip() and "No entries" not in stderr):
            return {"available": False, "reason": f"Cannot read the worker kernel log: {stderr.strip() or f'exit {rc}'}", "samples": []}

        per_rule: dict[Any, int] = {}
        samples = []
        for line in reversed(stdout.splitlines()):
            m = _SAMPLE.match(line.strip())
            if not m:
                continue
            time, mode, rule_id, rest = m.groups()
            rule = rule_id.replace("_", " ") if rule_id else None
            if per_rule.get(rule, 0) >= limit:
                continue
            per_rule[rule] = per_rule.get(rule, 0) + 1
            fields: dict[str, str] = {}
            for key, value in _FIELD.findall(rest.split(" [", 1)[0]):  # outer packet only
                fields.setdefault(key, value)
            samples.append({
                "time": time, "mode": mode, "rule": rule,
                "in": fields.get("IN"), "out": fields.get("OUT"), "src": fields.get("SRC"), "dst": fields.get("DST"),
                "proto": fields.get("PROTO"),
                "sport": int(fields["SPT"]) if fields.get("SPT", "").isdigit() else None,
                "dport": int(fields["DPT"]) if fields.get("DPT", "").isdigit() else None,
            })
        return {"available": True, "reason": None, "samples": samples}

    # ── NetworkPolicies ────────────────────────────────────────────────────

    def policies(self) -> dict[str, Any]:
        items = self._policies()
        roles = self._roles_map()
        grouped: dict[str, list[dict]] = {}
        for p in items:
            grouped.setdefault(p["metadata"]["namespace"], []).append(p)

        namespaces = []
        common: list[str] = []
        for ns in sorted(grouped):
            allow, egress_to, limited = [], [], False
            for p in sorted(grouped[ns], key=lambda p: p["metadata"]["name"]):
                name, spec = p["metadata"]["name"], p["spec"]
                app = (spec.get("podSelector") or {}).get("matchLabels", {}).get("app")
                if name == "allow-common-ingress" and not common:
                    common = self._common_sources(spec)
                if "Egress" in (spec.get("policyTypes") or []):
                    limited = True
                    egress_to += [self._describe_egress(rule) for rule in spec.get("egress") or []]
                    continue
                if name in ("allow-common-ingress", "default-deny-ingress"):
                    continue
                for rule in spec.get("ingress") or []:
                    for peer in rule.get("from") or []:
                        src = (peer.get("namespaceSelector") or {}).get("matchLabels", {}).get("kubernetes.io/metadata.name")
                        if src:
                            allow.append({"from": src, "to": app or "any pod", "policy": name})
            namespaces.append({
                "name": ns, "role": roles.get(ns, ""), "allow": allow,
                "egress": {"limited": limited, "to": egress_to},
                "policies": sorted(p["metadata"]["name"] for p in grouped[ns]),
            })

        unlisted: list[str] = []
        if namespaces:
            try:
                existing = [n.metadata.name for n in self.k8s.core.list_namespace().items] if self._list_policies is None else []
                unlisted = sorted(set(existing) - set(grouped))
            except Exception as exc:
                log.warning("isolation: cannot list namespaces: %s", exc)
        return {"enabled": bool(namespaces), "namespaces": namespaces, "unlisted": unlisted, "common_sources": common}

    @staticmethod
    def _common_sources(spec: dict) -> list[str]:
        out = []
        for rule in spec.get("ingress") or []:
            for peer in rule.get("from") or []:
                if "ipBlock" in peer:
                    out.append(peer["ipBlock"]["cidr"])
                elif "namespaceSelector" in peer:
                    out.append((peer["namespaceSelector"].get("matchLabels") or {}).get("kubernetes.io/metadata.name", "?"))
                elif "podSelector" in peer:
                    out.append("own namespace")
        return out

    @staticmethod
    def _describe_egress(rule: dict) -> str:
        ports = ", ".join(f"{p.get('port')}/{p.get('protocol', 'TCP')}" for p in rule.get("ports") or [])
        parts = []
        for peer in rule.get("to") or []:
            if "ipBlock" in peer:
                block = peer["ipBlock"]
                parts.append("public addresses (private ranges excluded)" if block["cidr"] == "0.0.0.0/0" and block.get("except")
                             else block["cidr"])
            else:
                ns = (peer.get("namespaceSelector") or {}).get("matchLabels", {}).get("kubernetes.io/metadata.name", "")
                app = (peer.get("podSelector") or {}).get("matchLabels", {}).get("app")
                parts.append(f"{ns}/{app}" if app else ns)
        text = ", ".join(parts) or "anywhere"
        return f"{text} ({ports})" if ports else text

    # ── Flow check ─────────────────────────────────────────────────────────

    def targets(self) -> dict[str, Any]:
        """What the flow check can start from and go to."""
        listed = sorted({p["metadata"]["namespace"] for p in self._policies()})
        namespaces = sorted(n.metadata.name for n in self.k8s.core.list_namespace().items)
        destinations = []
        for ns in listed:
            for svc in self.k8s.core.list_namespaced_service(namespace=ns).items:
                if not svc.spec.selector:
                    continue
                for port in svc.spec.ports or []:
                    destinations.append({"namespace": ns, "service": svc.metadata.name, "port": port.port,
                                         "protocol": port.protocol or "TCP"})
        sources = [{"id": ns, "label": ns} for ns in namespaces]
        sources += [{"id": "mgmt", "label": "Management network (VMs, LAN)"}, {"id": "node", "label": "Another node"}]
        return {"sources": sources, "destinations": destinations}

    def check(self, source: str, destination: dict) -> dict[str, Any]:
        from app.services.network_plan import plan_value

        namespaces = {n.metadata.name: (n.metadata.labels or {}) for n in self.k8s.core.list_namespace().items}
        if source == "mgmt":
            src = {"namespace": None, "labels": {}, "ip": plan_value("node_ips")["ansible"]}
        elif source == "node":
            cidr = next((n.spec.pod_cidr for n in self.k8s.core.list_node().items if n.spec.pod_cidr), "10.42.0.0/24")
            src = {"namespace": None, "labels": {}, "ip": cidr.split("/")[0]}
        elif source in namespaces:
            src = {"namespace": source, "labels": {}, "ip": None}
        else:
            raise ValueError(f"unknown source {source!r}")

        if destination.get("internet"):
            dst = {"namespace": None, "labels": {}, "port": 443, "protocol": "TCP", "ip": "1.1.1.1"}
        else:
            svc = self.k8s.core.read_namespaced_service(name=destination["service"], namespace=destination["namespace"])
            port = int(destination["port"])
            # Policies see the pod's port: the Service port's targetPort. A named
            # targetPort cannot be resolved without the pod spec and is left to
            # the evaluator, which treats named ports as a match.
            sp = next((x for x in svc.spec.ports or [] if x.port == port), None)
            target = sp.target_port if sp and sp.target_port is not None else port
            dst = {"namespace": destination["namespace"], "labels": svc.spec.selector or {},
                   "port": target if isinstance(target, int) else None,
                   "protocol": (sp.protocol if sp else None) or destination.get("protocol") or "TCP", "ip": None}
        return evaluate(self._policies(), namespaces, src, dst)

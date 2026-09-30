import ipaddress
import json
import logging
import socket
import struct
import subprocess
from pathlib import Path
from typing import Any

import yaml

from app.config import settings
from app.services.k8s_service import K8sService, nf_api_get, pod_port, thread_core
from app.services.ran_chain import build_chain
from app.services.network_plan import plan_value

log = logging.getLogger(__name__)

NAD_NAME = "n2-physical"
NAD_NAMESPACE = plan_value("namespace_5g")
AMF_DEPLOYMENT = "amf"
AMF_NAMESPACE = plan_value("namespace_5g")
NETWORK_ANNOTATION = "k8s.v1.cni.cncf.io/networks"
OVS_DS_NAME = "ds-net-setup-worker"
OVS_DS_NS = "kube-system"

ANSIBLE_DIR = "/home/vagrant/ansible-ro"
GROUP_VARS = Path(ANSIBLE_DIR) / "group_vars" / "all.yml"
TESTBED_ENV = Path("/vagrant/.testbed.env")
# Persisted by Vagrantfile trigger when worker reloads with PHYSICAL_RAN_BRIDGE (synced to ansible /vagrant)
HOST_NIC_APPLIED_PATH = Path("/vagrant/.physical_ran_bridge_applied")


def _read_host_nic_applied() -> str:
    """Read host NIC actually applied by Vagrant (from PHYSICAL_RAN_BRIDGE)."""
    try:
        if HOST_NIC_APPLIED_PATH.exists():
            return HOST_NIC_APPLIED_PATH.read_text().strip()
    except OSError:
        pass
    return ""


def _read_ran_bridge_mode_from_ovs_ds(k8s: K8sService) -> str | None:
    """Read RAN_BRIDGE_MODE from the OVS DaemonSet pod template (source of truth at runtime)."""
    try:
        ds = k8s.apps.read_namespaced_daemon_set(
            name=OVS_DS_NAME, namespace=OVS_DS_NS,
        )
        containers = ds.spec.template.spec.containers or []
        for c in containers:
            for e in (c.env or []):
                if e.name == "RAN_BRIDGE_MODE" and e.value is not None:
                    return e.value.strip()
    except Exception:
        pass
    return None


def _read_testbed_env() -> dict[str, str]:
    try:
        lines = TESTBED_ENV.read_text().splitlines()
    except FileNotFoundError:
        return {}
    return dict(ln.split("=", 1) for ln in lines if "=" in ln and not ln.startswith("#"))


def _read_ansible_config() -> dict[str, Any]:
    """Read physical RAN config from ansible group_vars/all.yml."""
    try:
        with open(GROUP_VARS) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        data = {}
    # all.yml derives physical_ran_enabled from PHYSICAL_RAN_ENABLED in
    # .testbed.env; read the flag at its source (the file the ran_attach/ran_detach pieces write).
    physical_ran_enabled = _read_testbed_env().get("PHYSICAL_RAN_ENABLED", "false").lower() == "true"
    ran_bridge_mode_raw = data.get("ran_bridge_mode", "n2_n3")
    # Resolve Jinja template if present (e.g. "{{ 'n2_n3' if ... else 'disabled' }}")
    if isinstance(ran_bridge_mode_raw, str) and "{{" in ran_bridge_mode_raw:
        ran_bridge_mode = "n2_n3" if physical_ran_enabled else "disabled"
    else:
        ran_bridge_mode = ran_bridge_mode_raw
    return {
        "physical_ran_enabled": physical_ran_enabled,
        "physical_ran_interface": data.get("physical_ran_interface") or "",
        "physical_ran_subnet": data["physical_ran_subnet"],
        "physical_ran_gateway": data["physical_ran_gateway"],
        "amf_physical_ran_ip": data["amf_physical_ran_ip"],
        # The gNB needs a route to the UPF's N3 address through the worker.
        "n3_subnet": data["n3_subnet"],
        "ran_bridge_mode": ran_bridge_mode,
    }


class RanService:
    def __init__(self, k8s: K8sService) -> None:
        self.k8s = k8s

    # ── SSH helper (read-only queries) ───────────────────────────

    def _ssh(self, command: str, timeout: int | None = None) -> str:
        wrapped = [
            "ssh",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "LogLevel=ERROR",
            "-o", "BatchMode=yes",
            settings.worker_ssh_host,
            command,
        ]
        proc = subprocess.run(
            wrapped, capture_output=True, text=True,
            timeout=timeout or settings.shell_timeout_seconds, check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"SSH failed ({proc.returncode}): {err}")
        return proc.stdout or ""

    # ── Ansible runner ───────────────────────────────────────────

    # ── Read-only checks ─────────────────────────────────────────

    def _detect_ran_interface(self, subnet: str) -> str | None:
        """Find worker physical NIC for RAN. After OVS setup, the IP is on br-ran,
        so we get the physical port from ovs-vsctl (exclude patch ports).
        Before OVS setup, we find by IP in subnet."""
        if not subnet:
            return None
        try:
            if self._br_ran_exists():
                ports = self._br_ran_ports()
                for p in ports:
                    if not p.startswith("patch-"):
                        return p
                return None
            prefix = subnet.split("/")[0].rsplit(".", 1)[0]
            prefix_re = prefix.replace(".", "\\.")
            out = self._ssh(
                f"ip -o addr show | grep '{prefix_re}\\.' | awk '{{print $2}}' | grep -v '^br-' | head -1"
            )
            return out.strip() or None
        except Exception:
            return None

    def _ran_iface(self, cfg: dict[str, Any] | None = None) -> str:
        """Name of the RAN NIC inside the worker VM (configured, else detected)."""
        cfg = cfg or _read_ansible_config()
        return cfg["physical_ran_interface"] or self._detect_ran_interface(cfg["physical_ran_subnet"]) or ""

    def _nic_state(self, iface: str) -> str:
        """The worker's RAN NIC: "missing" (not in the VM), "down" (present, link
        not brought up), "no_carrier" (up, nothing on the wire) or "up"."""
        if not iface:
            return "missing"
        try:
            links = json.loads(self._ssh(f"ip -j link show {iface} 2>/dev/null || echo '[]'"))
        except Exception:
            return "missing"
        if not links:
            return "missing"
        flags = links[0].get("flags", [])
        if "UP" not in flags:
            return "down"
        if "NO-CARRIER" in flags or links[0].get("operstate") != "UP":
            return "no_carrier"
        return "up"

    def _br_ran_exists(self) -> bool:
        out = self._ssh("sudo ovs-vsctl br-exists br-ran 2>/dev/null; echo $?").strip()
        return out == "0"

    def _br_ran_ports(self) -> list[str]:
        if not self._br_ran_exists():
            return []
        out = self._ssh("sudo ovs-vsctl list-ports br-ran")
        return [p.strip() for p in out.splitlines() if p.strip()]

    def _nad_exists(self) -> bool:
        nads = self.k8s.list_nads(NAD_NAMESPACE)
        return any(n["name"] == NAD_NAME for n in nads)

    def _get_amf_networks(self) -> list[dict[str, Any]]:
        dep = self.k8s.apps.read_namespaced_deployment(
            name=AMF_DEPLOYMENT, namespace=AMF_NAMESPACE,
        )
        ann = dep.spec.template.metadata.annotations or {}
        raw = ann.get(NETWORK_ANNOTATION, "[]")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return []

    def _amf_pods(self) -> list[dict[str, Any]]:
        """The AMF's pods as they run: ready, going away, with n2phy (from the
        pod's own network annotation, not the Deployment template)."""
        pods = self.k8s.core.list_namespaced_pod(namespace=AMF_NAMESPACE, label_selector="app=amf").items
        out = []
        # An evicted or finished pod stays listed but never runs again.
        for p in (p for p in pods if p.status.phase not in ("Succeeded", "Failed")):
            try:
                nets = json.loads((p.metadata.annotations or {}).get(NETWORK_ANNOTATION, "[]"))
            except json.JSONDecodeError:
                nets = []
            out.append({
                "ready": p.status.phase == "Running" and all(c.ready for c in (p.status.container_statuses or [])),
                "deleting": p.metadata.deletion_timestamp is not None,
                "phy": any(n.get("name") == NAD_NAME for n in nets),
            })
        return out

    def attachment_facts(self) -> dict[str, Any]:
        """What the ran_attach / ran_detach read-backs judge (ran_chain verdicts)."""
        br = self._br_ran_exists()
        ports = self._br_ran_ports() if br else []
        iface = self._ran_iface()
        return {
            "br_exists": br,
            "nic_on_bridge": bool(iface) and iface in ports,
            "amf_veth": any(p and p != "br-ran" and p != iface and not p.startswith("patch-") for p in ports),
            "nad_exists": self._nad_exists(),
            "amf_pods": self._amf_pods(),
        }

    # ── Composite status ─────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        cfg = _read_ansible_config()
        # Override ran_bridge_mode with runtime value from OVS DaemonSet env (source of truth)
        ds_bridge_mode = _read_ran_bridge_mode_from_ovs_ds(self.k8s)
        if ds_bridge_mode is not None:
            cfg = {**cfg, "ran_bridge_mode": ds_bridge_mode}

        iface = self._ran_iface(cfg)
        nic_state = self._nic_state(iface or "")
        bridge_detected = nic_state == "up"
        br_exists = self._br_ran_exists()
        br_ports = self._br_ran_ports() if br_exists else []
        nad_exists = self._nad_exists()

        amf_networks = self._get_amf_networks()
        amf_has_phy = any(n.get("name") == NAD_NAME for n in amf_networks)
        amf_phy_ip = None
        if amf_has_phy:
            entry = next((n for n in amf_networks if n.get("name") == NAD_NAME), None)
            if entry:
                ips = entry.get("ips", [])
                amf_phy_ip = ips[0].split("/")[0] if ips else None

        # Data-path attachment: the AMF's ovs-cni veth must actually be a port on
        # br-ran. ovs-cni attaches n2phy at pod creation, so a br-ran rebuilt under
        # a running AMF leaves the annotation present but the veth gone. br_ports is
        # already fetched; a port that is not the NIC, a patch, or the bridge itself
        # is the AMF veth. Without this, status reads green while NGAP is dead.
        amf_attached = amf_has_phy and any(
            p and p != "br-ran" and p != iface and not p.startswith("patch-")
            for p in br_ports
        )
        enabled = br_exists and nad_exists and amf_has_phy and amf_attached

        amf_pod_ready = False
        try:
            pods = self.k8s.core.list_namespaced_pod(
                namespace=AMF_NAMESPACE, label_selector="app=amf",
            )
            if pods.items:
                p = pods.items[0]
                amf_pod_ready = (
                    p.status.phase == "Running"
                    and all(c.ready for c in (p.status.container_statuses or []))
                )
        except Exception:
            pass

        upf_has_return_route = self._upf_return_route_active(cfg["physical_ran_subnet"])

        host_nic_applied = _read_host_nic_applied()

        # NF ports are read by name from the running AMF (where the NF is wired).
        gnb = self._physical_gnb(cfg["physical_ran_subnet"], pod_port(thread_core(), AMF_NAMESPACE, "amf", "ngap"))
        counts = self._ue_counts(gnb["gnb_id"] if gnb["connected"] else None)
        chain = build_chain({
            "intent_attached": _read_testbed_env().get("PHYSICAL_RAN_ENABLED", "false").strip().lower() == "true",
            "bridge_exists": br_exists,
            "nic_state": nic_state, "iface": iface or "", "host_nic": host_nic_applied or None,
            "nic_on_bridge": bool(iface) and iface in br_ports, "bridge_ports": br_ports,
            "amf_attached": amf_attached, "amf_ip": cfg["amf_physical_ran_ip"],
            "ngap_port": gnb["ngap_port"], "gnb_connected": gnb["connected"], "gnb_ip": gnb["ip"],
            "upf_route": upf_has_return_route, "subnet": cfg["physical_ran_subnet"],
            "n3_subnet": cfg["n3_subnet"], **counts,
        })

        return {
            "config": cfg,
            "bridge_detected": bridge_detected,
            "nic_state": nic_state,
            "enabled": enabled,
            "bridge_exists": br_exists,
            "bridge_ports": br_ports,
            "nad_exists": nad_exists,
            "amf_has_physical_ran": amf_has_phy,
            "amf_attached_to_bridge": amf_attached,
            "amf_physical_ip": amf_phy_ip,
            "ran_interface_detected": iface,
            "amf_pod_ready": amf_pod_ready,
            "upf_has_return_route": upf_has_return_route,
            "host_nic_applied": host_nic_applied or None,
            "chain": chain,
            "gnb": gnb,
            "counts": counts,
        }

    # ── OVS DaemonSet helpers ────────────────────────────────────

    # ── AMF annotation helpers (K8s API – fast & reliable) ──────

    def _upf_return_route_active(self, subnet: str) -> bool:
        """True if the running UPF-Cloud routes `subnet` over N3.

        Reads the kernel table, not the Deployment env: the env only states
        intent, and a route that never got installed leaves GTP-U downlink on
        the default route over N6. The UPF image has no `ip`, hence /proc/net/route.
        """
        try:
            pods = self.k8s.core.list_namespaced_pod(
                namespace=AMF_NAMESPACE, label_selector="app=upf-cloud",
            )
            running = [p for p in pods.items if p.status.phase == "Running"]
            if not running:
                return False
            out = self.k8s.exec_in_pod(
                AMF_NAMESPACE, running[0].metadata.name, ["cat", "/proc/net/route"], container="upf-cloud",
            ) or ""
        except Exception:
            return False
        want = ipaddress.ip_network(subnet)
        for line in out.splitlines()[1:]:
            f = line.split()
            if len(f) < 8:
                continue
            dst = socket.inet_ntoa(struct.pack("<I", int(f[1], 16)))
            mask = socket.inet_ntoa(struct.pack("<I", int(f[7], 16)))
            if f[0] == "n3" and ipaddress.ip_network(f"{dst}/{mask}") == want:
                return True
        return False

    def _physical_gnb(self, subnet: str, ngap_port: int | None) -> dict[str, Any]:
        """The gNB the AMF knows whose SCTP peer is on the physical RAN subnet.

        gnb-info also lists simulated gNBs (UERANSIM, on N2); only the one on
        the RAN subnet is the physical one. {} from the AMF (restarting,
        unreachable) reads as not connected."""
        data = nf_api_get(thread_core(), AMF_NAMESPACE, "amf", "metrics", "gnb-info")
        net = ipaddress.ip_network(subnet)
        # A gNB can leave a stale association next to its live one: look at the
        # ones with a completed NG Setup first.
        items = sorted(data.get("items", []) or [], key=lambda g: not (g.get("ng") or {}).get("setup_success"))
        for g in items:
            ng = g.get("ng") or {}
            peer = str((ng.get("sctp") or {}).get("peer", ""))
            ip = peer.split("]")[0].lstrip("[") if peer.startswith("[") else peer.rsplit(":", 1)[0]
            try:
                on_ran = ipaddress.ip_address(ip) in net
            except ValueError:
                continue
            if on_ran:
                port = (g.get("network") or {}).get("ngap_port") or ngap_port
                return {"connected": bool(ng.get("setup_success")), "ip": ip,
                        "gnb_id": g.get("gnb_id"), "ngap_port": int(port) if port else None}
        return {"connected": False, "ip": None, "gnb_id": None, "ngap_port": ngap_port}

    def _ue_counts(self, gnb_id: int | None) -> dict[str, int]:
        """UEs registered through this gNB and their PDU sessions (AMF ue-info)."""
        if gnb_id is None:
            return {"ues": 0, "pdu": 0}
        items: list[dict[str, Any]] = []
        page = 0
        while True:
            data = nf_api_get(thread_core(), AMF_NAMESPACE, "amf", "metrics", f"ue-info?page={page}&page_size=100")
            batch = data.get("items", []) or []
            items.extend(batch)
            if not batch or len(items) >= (data.get("pager") or {}).get("count", 0):
                break
            page += 1
        mine = [u for u in items if (u.get("gnb") or {}).get("gnb_id") == gnb_id]
        return {"ues": len(mine), "pdu": sum(int(u.get("pdu_sessions_count") or 0) for u in mine)}

    # ── Enable / disable via Ansible + direct K8s patch ──────────


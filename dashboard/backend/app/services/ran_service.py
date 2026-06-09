import json
import logging
import subprocess
import time
from pathlib import Path
from collections.abc import Callable
from typing import Any

import yaml

from app.config import settings
from app.services.k8s_service import K8sService

log = logging.getLogger(__name__)

NAD_NAME = "n2-physical"
NAD_NAMESPACE = "5g"
AMF_DEPLOYMENT = "amf"
AMF_NAMESPACE = "5g"
NETWORK_ANNOTATION = "k8s.v1.cni.cncf.io/networks"
OVS_DS_LABEL = "app=ds-net-setup-worker"
OVS_DS_NAME = "ds-net-setup-worker"
OVS_DS_NS = "kube-system"

ANSIBLE_DIR = "/home/vagrant/ansible-ro"
ANSIBLE_CFG = f"{ANSIBLE_DIR}/ansible.cfg"
ANSIBLE_PLAYBOOK_BIN = "/home/vagrant/.local/bin/ansible-playbook"
GROUP_VARS = Path(ANSIBLE_DIR) / "group_vars" / "all.yml"
# Persisted by Vagrantfile trigger when worker reloads with PHYSICAL_RAN_BRIDGE (synced to ansible /vagrant)
HOST_NIC_APPLIED_PATH = Path("/vagrant/.physical_ran_bridge_applied")
PHASE4_PLAYBOOK = f"{ANSIBLE_DIR}/phases/04-overlay-network/playbook.yml"
PHASE5_PLAYBOOK = f"{ANSIBLE_DIR}/phases/05-5g-core/playbook.yml"


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


def _read_ansible_config() -> dict[str, Any]:
    """Read physical RAN config from ansible group_vars/all.yml."""
    try:
        with open(GROUP_VARS) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        data = {}
    physical_ran_enabled = bool(data.get("physical_ran_enabled", False))
    ran_bridge_mode_raw = data.get("ran_bridge_mode", "n2_n3")
    # Resolve Jinja template if present (e.g. "{{ 'n2_n3' if ... else 'disabled' }}")
    if isinstance(ran_bridge_mode_raw, str) and "{{" in ran_bridge_mode_raw:
        ran_bridge_mode = "n2_n3" if physical_ran_enabled else "disabled"
    else:
        ran_bridge_mode = ran_bridge_mode_raw
    return {
        "physical_ran_enabled": physical_ran_enabled,
        "physical_ran_interface": data.get("physical_ran_interface") or "",
        "physical_ran_subnet": data.get("physical_ran_subnet", "192.168.6.0/24"),
        "amf_physical_ran_ip": data.get("amf_physical_ran_ip", "192.168.6.150"),
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

    def _run_playbook(
        self, playbook: str, tags: list[str],
        extra_vars: dict[str, str], timeout: int = 120,
    ) -> str:
        cmd = [ANSIBLE_PLAYBOOK_BIN, playbook, "--tags", ",".join(tags)]
        for k, v in extra_vars.items():
            cmd.extend(["-e", f"{k}={v}"])

        import os
        env = {**os.environ, "ANSIBLE_CONFIG": ANSIBLE_CFG}

        log.info("Running: %s", " ".join(cmd))
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, cwd=ANSIBLE_DIR, env=env, check=False,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            log.error("Playbook failed (rc=%d):\n%s", proc.returncode, output[-2000:])
            raise RuntimeError(
                f"Ansible playbook failed (rc={proc.returncode}): {output[-1000:]}"
            )
        log.info("Playbook completed successfully")
        return output

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

    def _interface_detected(self, iface: str, subnet: str = "") -> bool:
        """Check if the bridged interface exists and is UP on the worker."""
        if not iface and subnet:
            iface = self._detect_ran_interface(subnet) or ""
        if not iface:
            return False
        try:
            out = self._ssh(f"ip -j link show {iface} 2>/dev/null || echo '[]'")
            links = json.loads(out)
            if not links:
                return False
            return links[0].get("operstate", "DOWN") == "UP"
        except Exception:
            return False

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

    # ── Composite status ─────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        cfg = _read_ansible_config()
        # Override ran_bridge_mode with runtime value from OVS DaemonSet env (source of truth)
        ds_bridge_mode = _read_ran_bridge_mode_from_ovs_ds(self.k8s)
        if ds_bridge_mode is not None:
            cfg = {**cfg, "ran_bridge_mode": ds_bridge_mode}

        iface = cfg["physical_ran_interface"] or self._detect_ran_interface(cfg["physical_ran_subnet"])
        bridge_detected = self._interface_detected(iface or "", cfg["physical_ran_subnet"])
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

        enabled = br_exists and nad_exists and amf_has_phy

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

        upf_has_return_route = self._upf_has_physical_ran_subnet()

        host_nic_applied = _read_host_nic_applied()

        return {
            "config": cfg,
            "bridge_detected": bridge_detected,
            "enabled": enabled,
            "bridge_exists": br_exists,
            "bridge_ports": br_ports,
            "nad_exists": nad_exists,
            "amf_has_physical_ran": amf_has_phy,
            "amf_physical_ip": amf_phy_ip,
            "ran_interface_detected": iface,
            "amf_pod_ready": amf_pod_ready,
            "upf_has_return_route": upf_has_return_route,
            "host_nic_applied": host_nic_applied or None,
        }

    # ── OVS DaemonSet helpers ────────────────────────────────────

    def _restart_ovs_ds_pod(self) -> None:
        """Delete the worker OVS DaemonSet pod so the DS controller recreates
        it and the setup script re-runs (creating/removing br-ran)."""
        pods = self.k8s.core.list_namespaced_pod(
            namespace=OVS_DS_NS, label_selector=OVS_DS_LABEL,
        )
        for pod in pods.items:
            self.k8s.core.delete_namespaced_pod(
                name=pod.metadata.name, namespace=OVS_DS_NS,
            )
            log.info("Deleted OVS DS pod %s to force re-execution", pod.metadata.name)

    def _wait_for_bridge(
        self,
        bridge: str = "br-ran",
        retries: int = 40,
        delay: float = 4,
        on_progress: Callable[[str, str, str], None] | None = None,
    ) -> bool:
        """Poll the worker until the OVS bridge exists."""
        for i in range(retries):
            if on_progress:
                on_progress("ovs_bridge_created", "in_progress", f"Waiting for {bridge} ({i + 1}/{retries})…")
            try:
                out = self._ssh(
                    f"sudo ovs-vsctl br-exists {bridge} 2>/dev/null; echo $?",
                    timeout=8,
                ).strip()
                if out == "0":
                    log.info("Bridge %s detected after %d checks", bridge, i + 1)
                    return True
            except Exception:
                pass
            time.sleep(delay)
        log.warning("Bridge %s not found after %d retries", bridge, retries)
        return False

    # ── AMF annotation helpers (K8s API – fast & reliable) ──────

    def _patch_amf_networks(self, networks: list[dict[str, Any]]) -> None:
        """Directly patch the AMF deployment's Multus network annotation."""
        dep = self.k8s.apps.read_namespaced_deployment(
            name=AMF_DEPLOYMENT, namespace=AMF_NAMESPACE,
        )
        ann = dep.spec.template.metadata.annotations or {}
        ann[NETWORK_ANNOTATION] = json.dumps(networks) + "\n"
        dep.spec.template.metadata.annotations = ann
        self.k8s.apps.patch_namespaced_deployment(
            name=AMF_DEPLOYMENT, namespace=AMF_NAMESPACE, body=dep,
        )
        log.info("Patched AMF networks annotation: %s", [n["name"] for n in networks])

    def _amf_networks_without_physical(self) -> list[dict[str, Any]]:
        return [n for n in self._get_amf_networks() if n.get("name") != NAD_NAME]

    def _amf_networks_with_physical(self, ip: str) -> list[dict[str, Any]]:
        nets = self._amf_networks_without_physical()
        nets.append({"name": NAD_NAME, "interface": "n2phy", "ips": [f"{ip}/24"]})
        return nets

    def _upf_has_physical_ran_subnet(self) -> bool:
        """Check if UPF-Cloud has PHYSICAL_RAN_SUBNET env (return route to gNB)."""
        try:
            dep = self.k8s.apps.read_namespaced_deployment(
                name="upf-cloud", namespace=AMF_NAMESPACE,
            )
            env = dep.spec.template.spec.containers[0].env or []
            return any(e.name == "PHYSICAL_RAN_SUBNET" and (e.value or "").strip() for e in env)
        except Exception:
            return False

    def _patch_upf_physical_ran_subnet(self, subnet: str) -> None:
        """Add PHYSICAL_RAN_SUBNET env to UPF-Cloud so init adds return route to gNB."""
        dep = self.k8s.apps.read_namespaced_deployment(
            name="upf-cloud", namespace=AMF_NAMESPACE,
        )
        env = list(dep.spec.template.spec.containers[0].env or [])
        existing = next((e for e in env if e.name == "PHYSICAL_RAN_SUBNET"), None)
        if existing:
            existing.value = subnet
        else:
            from kubernetes.client import V1EnvVar
            env.append(V1EnvVar(name="PHYSICAL_RAN_SUBNET", value=subnet))
        dep.spec.template.spec.containers[0].env = env
        self.k8s.apps.patch_namespaced_deployment(
            name="upf-cloud", namespace=AMF_NAMESPACE, body=dep,
        )
        self.k8s.restart_deployment(AMF_NAMESPACE, "upf-cloud")
        log.info("Patched UPF with PHYSICAL_RAN_SUBNET=%s and restarted", subnet)

    def _remove_upf_physical_ran_subnet(self) -> None:
        """Remove PHYSICAL_RAN_SUBNET from UPF-Cloud."""
        dep = self.k8s.apps.read_namespaced_deployment(
            name="upf-cloud", namespace=AMF_NAMESPACE,
        )
        env = [e for e in (dep.spec.template.spec.containers[0].env or []) if e.name != "PHYSICAL_RAN_SUBNET"]
        dep.spec.template.spec.containers[0].env = env
        self.k8s.apps.patch_namespaced_deployment(
            name="upf-cloud", namespace=AMF_NAMESPACE, body=dep,
        )
        self.k8s.restart_deployment(AMF_NAMESPACE, "upf-cloud")
        log.info("Removed PHYSICAL_RAN_SUBNET from UPF")

    # ── Enable / disable via Ansible + direct K8s patch ──────────

    def enable(self, on_progress: Callable[[str, str, str], None] | None = None) -> dict[str, Any]:
        cfg = _read_ansible_config()
        steps: list[dict[str, Any]] = []

        def prog(step: str, status: str, msg: str) -> None:
            if on_progress:
                on_progress(step, status, msg)

        # Pre-flight: detect NIC on the worker
        prog("nic_check", "in_progress", "Checking worker VM NIC…")
        iface = cfg["physical_ran_interface"] or self._detect_ran_interface(cfg["physical_ran_subnet"])
        nic_present = self._interface_detected(iface or "", cfg["physical_ran_subnet"]) if iface else False
        if not nic_present:
            prog("nic_check", "error", "Worker VM NIC not found")
            steps.append({
                "step": "nic_check",
                "status": "error",
                "hint": (
                    "No RAN interface detected on the worker VM. "
                    "The VM was likely started without PHYSICAL_RAN_BRIDGE. "
                    "Run: PHYSICAL_RAN_ENABLED=true PHYSICAL_RAN_BRIDGE=<host_nic> vagrant reload worker"
                ),
            })
            return {
                "enabled": False,
                "steps": steps,
                "error": "Worker VM has no RAN interface. Re-add it with vagrant reload worker.",
            }
        prog("nic_check", "ok", f"Found {iface}")
        steps.append({"step": "nic_check", "status": "ok"})

        br_exists = self._br_ran_exists()
        nad_exists = self._nad_exists()
        amf_has_phy = any(n.get("name") == NAD_NAME for n in self._get_amf_networks())
        upf_has_route = self._upf_has_physical_ran_subnet()
        bridge_just_created = False

        extra = {
            "physical_ran_enabled": "true",
            "physical_ran_interface": cfg["physical_ran_interface"] or "",
            "ran_bridge_mode": "n2_n3",
            "amf_physical_ran_ip": cfg["amf_physical_ran_ip"],
        }
        log.info(
            "Enabling physical RAN (br=%s nad=%s amf=%s upf=%s)",
            br_exists, nad_exists, amf_has_phy, upf_has_route,
        )

        # 1. OVS DaemonSet: only if br-ran not present (skip when already exists)
        if not br_exists:
            prog("ovs_daemonset_updated", "in_progress", "Running Ansible overlay playbook…")
            self._run_playbook(PHASE4_PLAYBOOK, tags=["overlay"], extra_vars=extra)
            steps.append({"step": "ovs_daemonset_updated", "status": "ok"})
            prog("ovs_daemonset_updated", "ok", "Done")

            prog("ovs_bridge_created", "in_progress", "Restarting OVS DaemonSet pod…")
            self._restart_ovs_ds_pod()
            found = self._wait_for_bridge("br-ran", retries=40, delay=4, on_progress=prog)
            hint = None if found else (
                "br-ran not detected on worker within ~2.5min. Check: "
                "1) OVS DaemonSet pod (kube-system, app=ds-net-setup-worker) is Running; "
                "2) SSH to worker works (backend uses worker_ssh_host); "
                "3) physical_ran_interface in group_vars/all.yml matches the worker NIC."
            )
            prog("ovs_bridge_created", "ok" if found else "warning", "Done" if found else (hint or ""))
            steps.append({
                "step": "ovs_bridge_created",
                "status": "ok" if found else "warning",
                "hint": hint,
            })
            if not found:
                return {
                    "enabled": False,
                    "steps": steps,
                    "error": "br-ran not detected. Aborting to avoid stuck AMF pod.",
                }
            bridge_just_created = True
        else:
            prog("ovs_daemonset_updated", "ok", "Skipped (br-ran already exists)")
            steps.append({"step": "ovs_daemonset_updated", "status": "ok (skipped)"})
            prog("ovs_bridge_created", "ok", "Already existed")
            steps.append({"step": "ovs_bridge_created", "status": "ok (already existed)"})

        # Debounce: Ansible preflight on the worker races right after OVS creates br-ran.
        if bridge_just_created:
            time.sleep(4)
            if not self._br_ran_exists():
                return {
                    "enabled": False,
                    "steps": steps,
                    "error": "br-ran disappeared on worker after setup. Check OVS DaemonSet logs.",
                }

        # 2. NAD: only if missing
        # The multus play can take several minutes; 120s was too tight for dashboard enables.
        # Ansible may also skip NAD creation if preflight still misses the bridge (exit 0) —
        # we verify the object exists before continuing.
        if not nad_exists:
            prog("nad_creation", "in_progress", "Creating NAD via Ansible…")

            def _run_nad_ansible() -> None:
                self._run_playbook(
                    PHASE4_PLAYBOOK, tags=["nad"], extra_vars=extra, timeout=420,
                )

            _run_nad_ansible()
            if not self._nad_exists() and self._br_ran_exists():
                prog("nad_creation", "in_progress", "NAD still missing; retrying after preflight delay…")
                time.sleep(12)
                _run_nad_ansible()
            if not self._nad_exists():
                prog("nad_creation", "error", "n2-physical not found in cluster after Ansible")
                steps.append({
                    "step": "nad_creation",
                    "status": "error",
                    "hint": (
                        "Ansible finished but NAD was not created (often a preflight race or skipped task). "
                        "On the worker run: sudo ovs-vsctl br-exists br-ran && echo OK. "
                        "Then click Reconfigure or Enable again."
                    ),
                })
                return {
                    "enabled": False,
                    "steps": steps,
                    "error": (
                        "NAD n2-physical was not created. "
                        "Confirm br-ran on the worker, then Reconfigure."
                    ),
                }
            steps.append({"step": "nad_creation", "status": "ok"})
            prog("nad_creation", "ok", "Done")
        else:
            prog("nad_creation", "ok", "Already existed")
            steps.append({"step": "nad_creation", "status": "ok (already existed)"})

        # 3. AMF annotation: only if missing
        if not amf_has_phy:
            prog("amf_annotation_patched", "in_progress", "Patching AMF deployment…")
            new_nets = self._amf_networks_with_physical(cfg["amf_physical_ran_ip"])
            self._patch_amf_networks(new_nets)
            steps.append({"step": "amf_annotation_patched", "status": "ok"})
            prog("amf_annotation_patched", "ok", "Done")
        else:
            prog("amf_annotation_patched", "ok", "Already existed")
            steps.append({"step": "amf_annotation_patched", "status": "ok (already existed)"})

        # 4. UPF return route: only if missing
        if not upf_has_route:
            prog("upf_return_route", "in_progress", "Patching UPF and restarting…")
            self._patch_upf_physical_ran_subnet(cfg["physical_ran_subnet"])
            steps.append({"step": "upf_return_route", "status": "ok"})
            prog("upf_return_route", "ok", "Done")
        else:
            prog("upf_return_route", "ok", "Already existed")
            steps.append({"step": "upf_return_route", "status": "ok (already existed)"})

        return {"enabled": True, "steps": steps}

    def disable(self, on_progress: Callable[[str, str, str], None] | None = None) -> dict[str, Any]:
        log.info("Disabling physical RAN")
        steps: list[dict[str, Any]] = []

        def prog(step: str, status: str, msg: str) -> None:
            if on_progress:
                on_progress(step, status, msg)

        # 1. Remove n2-physical from AMF annotation first (fast, prevents stuck pods)
        prog("amf_annotation_patched", "in_progress", "Removing n2-physical from AMF…")
        new_nets = self._amf_networks_without_physical()
        self._patch_amf_networks(new_nets)
        steps.append({"step": "amf_annotation_patched", "status": "ok"})
        prog("amf_annotation_patched", "ok", "Done")

        extra = {
            "physical_ran_enabled": "false",
            "ran_bridge_mode": "disabled",
        }

        # 2. Clean up NAD
        prog("nad_cleanup", "in_progress", "Removing NAD via Ansible…")
        self._run_playbook(PHASE4_PLAYBOOK, tags=["nad"], extra_vars=extra)
        steps.append({"step": "nad_cleanup", "status": "ok"})
        prog("nad_cleanup", "ok", "Done")

        # 3. Remove PHYSICAL_RAN_SUBNET from UPF
        prog("upf_return_route_removed", "in_progress", "Removing UPF return route…")
        try:
            self._remove_upf_physical_ran_subnet()
            steps.append({"step": "upf_return_route_removed", "status": "ok"})
            prog("upf_return_route_removed", "ok", "Done")
        except Exception as exc:
            log.warning("UPF patch failed: %s", exc)
            steps.append({"step": "upf_return_route_removed", "status": "skipped"})
            prog("upf_return_route_removed", "skipped", "Skipped")

        # 4. Remove OVS bridge via SSH
        prog("ovs_bridge_removed", "in_progress", "Removing br-ran via SSH…")
        try:
            self._ssh(
                "sudo ovs-vsctl --if-exists del-port br-n2 patch-n2-ran && "
                "sudo ovs-vsctl --if-exists del-port br-n3 patch-n3-ran && "
                "sudo ovs-vsctl --if-exists del-br br-ran",
                timeout=15,
            )
            steps.append({"step": "ovs_bridge_removed", "status": "ok"})
            prog("ovs_bridge_removed", "ok", "Done")
        except RuntimeError as exc:
            log.warning("OVS teardown failed: %s", exc)
            steps.append({"step": "ovs_bridge_removed", "status": "skipped"})
            prog("ovs_bridge_removed", "skipped", "Skipped")

        # 5. Reset DaemonSet env to disabled (prevents br-ran re-creation on pod restart)
        prog("ovs_daemonset_reset", "in_progress", "Resetting OVS DaemonSet…")
        self._run_playbook(PHASE4_PLAYBOOK, tags=["overlay"], extra_vars=extra)
        steps.append({"step": "ovs_daemonset_reset", "status": "ok"})
        prog("ovs_daemonset_reset", "ok", "Done")

        return {"enabled": False, "steps": steps}

import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml
from kubernetes.client.exceptions import ApiException

from app.services.k8s_service import K8sService

log = logging.getLogger(__name__)

NS = "5g"
STATE_CM = "ueransim-dashboard-state"
TOPOLOGY_VARS = Path("/home/vagrant/ansible-ro/phases/06-ueransim-mec/vars/topology.yml")
GNB_DEFAULTS_PATH = Path("/home/vagrant/ansible-ro/phases/06-ueransim-mec/roles/gnb_deployment/defaults/main.yml")
K8S_API_SERVER = "192.168.56.10:6443"
DISCOVERY_IMAGE = "nicolaka/netshoot:latest"
GNB_BINARY = "/UERANSIM/build/nr-gnb"
UE_BINARY = "/UERANSIM/build/nr-ue"


class UeransimService:
    def __init__(self, k8s: K8sService) -> None:
        self.k8s = k8s

    # ── Topology / config readers ────────────────────────────────

    def _read_full_topology(self) -> dict[str, Any]:
        try:
            with open(TOPOLOGY_VARS) as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            return {}

    def _read_topology_defaults(self) -> dict[str, Any]:
        data = self._read_full_topology()
        defaults = (data.get("ueransim_topology") or {}).get("defaults") or {}
        return {
            "image": defaults.get("image", "jacobbista/comnetsemu-ueransim:latest"),
            "mcc": defaults.get("mcc", "001"),
            "mnc": defaults.get("mnc", "01"),
            "imsi_msin_base": defaults.get("imsi_msin_base", "1234567"),
            "key": defaults.get("key_template", "8baf473f2f8fd09487cccbd7097c6862"),
            "op": defaults.get("op", "11111111111111111111111111111111"),
            "node_defaults": defaults.get("node_defaults", {"gnb": "edge", "ue": "edge"}),
        }

    def _read_discovery_token(self) -> str:
        try:
            cm = self.k8s.get_configmap(NS, "discovery-token")
            return (cm.get("data") or {}).get("token", "")
        except Exception:
            return ""

    def _read_k8s_api_server(self) -> str:
        try:
            with open(GNB_DEFAULTS_PATH) as f:
                data = yaml.safe_load(f) or {}
            return data.get("k8s_api_server", K8S_API_SERVER)
        except FileNotFoundError:
            return K8S_API_SERVER

    # ── Smart defaults for the frontend ──────────────────────────

    def get_defaults(self) -> dict[str, Any]:
        """Return everything the frontend needs to render smart creation forms."""
        topo = self._read_full_topology()
        defaults = self._read_topology_defaults()
        gnbs = self.list_gnbs()
        ues = self.list_ues()
        nodes = [{"name": n.name, "roles": n.roles, "status": n.status} for n in self.k8s.list_nodes()]

        existing_cell_ids = set()
        existing_gnb_numbers = []
        for g in gnbs:
            cid = g.get("labels", {}).get("cell-id")
            if cid:
                existing_cell_ids.add(int(cid))
            m = re.search(r"gnb-(\d+)", g["name"])
            if m:
                existing_gnb_numbers.append(int(m.group(1)))

        existing_ue_numbers = []
        for u in ues:
            m = re.search(r"ue-(\d+)$", u["name"])
            if m:
                existing_ue_numbers.append(int(m.group(1)))
            m2 = re.search(r"ue-cell-(\d+)", u["name"])
            if m2:
                existing_ue_numbers.append(int(m2.group(1)))

        next_gnb_num = max(existing_gnb_numbers, default=0) + 1
        next_cell_id = max(existing_cell_ids, default=0) + 1
        next_ue_num = max(existing_ue_numbers, default=0) + 1

        cells_from_topology = (topo.get("ueransim_topology") or {}).get("cells") or []

        return {
            "defaults": defaults,
            "gnbs": gnbs,
            "ues": ues,
            "cells": [{"id": c["id"], "gnb_name": c["gnb"]["name"]} for c in cells_from_topology],
            "existing_cell_ids": sorted(existing_cell_ids),
            "next_gnb_name": f"gnb-{next_gnb_num}",
            "next_cell_id": next_cell_id,
            "next_ue_name": f"ue-{next_ue_num}",
            "has_discovery_token": bool(self._read_discovery_token()),
        }

    # ── State persistence ────────────────────────────────────────

    def _get_state(self) -> dict[str, Any]:
        try:
            cm = self.k8s.get_configmap(NS, STATE_CM)
            raw = (cm.get("data") or {}).get("state", "{}")
            parsed = json.loads(raw)
            return {
                "replicas": parsed.get("replicas", {"gnbs": {}, "ues": {}}),
                "forms": parsed.get("forms", {"gnbs": {}, "ues": {}}),
            }
        except Exception:
            return {"replicas": {"gnbs": {}, "ues": {}}, "forms": {"gnbs": {}, "ues": {}}}

    def _save_state(self, state: dict[str, Any]) -> None:
        self.k8s.apply_configmap(NS, STATE_CM, {"state": json.dumps(state)})

    def _record_form(self, kind: str, name: str, payload: dict[str, Any]) -> None:
        state = self._get_state()
        state.setdefault("forms", {}).setdefault(kind, {})[name] = payload
        self._save_state(state)

    def _delete_form(self, kind: str, name: str) -> None:
        state = self._get_state()
        state.setdefault("forms", {}).setdefault(kind, {}).pop(name, None)
        self._save_state(state)

    # ── Validation ───────────────────────────────────────────────

    def _validate_gnb_payload(self, payload: dict[str, Any]) -> None:
        pass  # name auto-generated, node always edge

    def _validate_ue_payload(self, payload: dict[str, Any]) -> None:
        gnb_name = str(payload.get("gnb_name", "")).strip()
        if not gnb_name:
            raise ValueError("gnb_name is required (which gNB to connect to)")
        existing = {g["name"] for g in self.list_gnbs()}
        if gnb_name not in existing:
            raise ValueError(f"gNB '{gnb_name}' not found, deploy it first")

    # ── Manifest builders (real UERANSIM with discovery) ─────────

    def _build_gnb_manifest_from_form(self, payload: dict[str, Any]) -> dict[str, Any]:
        defaults = self._read_topology_defaults()
        token = self._read_discovery_token()
        api_server = self._read_k8s_api_server()

        name = payload["name"]
        cell_id = int(payload.get("cell_id", 1))
        nci = payload.get("nci", f"0x{cell_id:09X}")
        tac = int(payload.get("tac", 1))
        mcc = payload.get("mcc") or defaults["mcc"]
        mnc = payload.get("mnc") or defaults["mnc"]
        slices = payload.get("slices") or [{"sst": 1, "sd": 1}]
        node = "edge"  # always edge
        replicas = 1  # one gNB = one antenna, no scaling
        image = payload.get("image") or defaults["image"]

        slices_yaml = "\n".join(
            f"                - sst: {s['sst']}\n                  sd: {s['sd']}" for s in slices
        )

        config_script = f"""set -euo pipefail
echo "Waiting for network interfaces (n2, n3)..."
for i in $(seq 1 30); do
  N2_READY=$(ip addr show n2 2>/dev/null | grep -c "inet " || echo 0)
  N3_READY=$(ip addr show n3 2>/dev/null | grep -c "inet " || echo 0)
  if [ "$N2_READY" -gt 0 ] && [ "$N3_READY" -gt 0 ]; then
    echo "Network interfaces ready"
    break
  fi
  sleep 1
done
AMF_IP=$(cat /config/amf-ip)
echo "Generating gNB config with AMF IP: $AMF_IP"
cat > /config/gnb.yaml <<EOF
mcc: '{mcc}'
mnc: '{mnc}'
nci: '{nci}'
idLength: 32
tac: {tac}
linkIp: 0.0.0.0
ngapIp: 0.0.0.0
gtpIp: 0.0.0.0
gtpPort: 2152
ngapPort: 38412
amfConfigs:
  - address: $AMF_IP
    port: 38412
slices:
{slices_yaml}
ignoreStreamIds: true
EOF
cat /config/gnb.yaml"""

        discovery_script = f"""set -e
DEFAULT_GW=$(ip route | grep -E '^10\\.[0-9]+\\.[0-9]+\\.0/24 dev eth0' | sed 's|.*/24.*||;s|.*\\.||')
ETH0_NET=$(ip route | grep -E '^10\\.[0-9]+\\.[0-9]+\\.0/24 dev eth0' | awk '{{print $1}}' | sed 's|\\.0/24||')
if [ -n "$ETH0_NET" ] && ! ip route | grep -q "^default"; then
  ip route add default via ${{ETH0_NET}}.1 dev eth0 2>/dev/null || true
fi
echo "Discovering AMF N2 IP for cell {cell_id}..."
for i in $(seq 1 10); do
  RESP=$(curl -sk -H "Authorization: Bearer $DISCOVERY_TOKEN" \
    "${{K8S_API}}/api/v1/namespaces/${{NAMESPACE}}/pods?labelSelector=app=amf" 2>/dev/null || echo "{{}}")
  AMF_IP=$(echo "$RESP" | jq -r --arg iface "n2c{cell_id}" '
    .items[0].metadata.annotations["k8s.v1.cni.cncf.io/network-status"] // empty
    | fromjson | .[] | select(.interface == $iface) | .ips[0] // empty
  ' 2>/dev/null || echo "")
  if [ -n "$AMF_IP" ] && [ "$AMF_IP" != "null" ]; then
    echo "Found AMF N2 IP: $AMF_IP"
    echo "$AMF_IP" > /config/amf-ip
    exit 0
  fi
  echo "Waiting for AMF... (attempt $i/10)"
  sleep 2
done
echo "ERROR: Could not discover AMF IP"
exit 1"""

        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": name,
                "namespace": NS,
                "labels": {"app": name, "component": "gnb", "managed-by": "dashboard", "cell-id": str(cell_id)},
            },
            "spec": {
                "replicas": replicas,
                "selector": {"matchLabels": {"app": name}},
                "template": {
                    "metadata": {
                        "labels": {"app": name, "component": "gnb", "managed-by": "dashboard", "cell-id": str(cell_id)},
                        "annotations": {
                            "k8s.v1.cni.cncf.io/networks": json.dumps([
                                {"name": f"n2-cell-{cell_id}", "interface": "n2"},
                                {"name": f"n3-cell-{cell_id}", "interface": "n3"},
                            ]),
                        },
                    },
                    "spec": {
                        "automountServiceAccountToken": False,
                        "nodeSelector": {"kubernetes.io/hostname": node},
                        "volumes": [{"name": "gnb-config-runtime", "emptyDir": {}}],
                        "initContainers": [
                            {
                                "name": "amf-discovery",
                                "image": DISCOVERY_IMAGE,
                                "imagePullPolicy": "IfNotPresent",
                                "securityContext": {"capabilities": {"add": ["NET_ADMIN"]}},
                                "env": [
                                    {"name": "K8S_API", "value": f"https://{api_server}"},
                                    {"name": "NAMESPACE", "value": NS},
                                    {"name": "CELL_ID", "value": str(cell_id)},
                                    {"name": "DISCOVERY_TOKEN", "value": token},
                                ],
                                "command": ["/bin/sh", "-c"],
                                "args": [discovery_script],
                                "volumeMounts": [{"name": "gnb-config-runtime", "mountPath": "/config"}],
                            },
                            {
                                "name": "config-gen",
                                "image": image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": ["/bin/bash", "-c"],
                                "args": [config_script],
                                "volumeMounts": [{"name": "gnb-config-runtime", "mountPath": "/config"}],
                            },
                        ],
                        "containers": [
                            {
                                "name": "gnb",
                                "image": image,
                                "imagePullPolicy": "IfNotPresent",
                                "securityContext": {"privileged": True, "capabilities": {"add": ["NET_ADMIN", "SYS_ADMIN"]}},
                                "command": [GNB_BINARY],
                                "args": ["-c", "/config/gnb.yaml"],
                                "ports": [
                                    {"containerPort": 38412, "name": "ngap", "protocol": "SCTP"},
                                    {"containerPort": 2152, "name": "gtpu", "protocol": "UDP"},
                                ],
                                "volumeMounts": [{"name": "gnb-config-runtime", "mountPath": "/config"}],
                                "readinessProbe": {
                                    "exec": {"command": ["pgrep", "-f", "nr-gnb"]},
                                    "initialDelaySeconds": 10, "periodSeconds": 5,
                                },
                                "livenessProbe": {
                                    "exec": {"command": ["pgrep", "-f", "nr-gnb"]},
                                    "initialDelaySeconds": 30, "periodSeconds": 10,
                                },
                                "resources": payload.get("resources") or {
                                    "requests": {"cpu": "500m", "memory": "512Mi"},
                                    "limits": {"cpu": "1000m", "memory": "1Gi"},
                                },
                            }
                        ],
                    },
                },
            },
        }

    def _build_ue_manifest_from_form(self, payload: dict[str, Any]) -> dict[str, Any]:
        defaults = self._read_topology_defaults()
        token = self._read_discovery_token()
        api_server = self._read_k8s_api_server()

        name = payload["name"]
        cell_id = int(payload.get("cell_id", 1))
        gnb_name = payload.get("gnb_name", f"gnb-{cell_id}")
        node = "edge"  # always edge
        replicas = 1  # one UE = one device, no scaling
        image = payload.get("image") or defaults["image"]
        mcc = defaults["mcc"]
        mnc = defaults["mnc"]
        apn = payload.get("apn", "internet")
        sst = int(payload.get("sst", 1))
        sd = int(payload.get("sd", 1))
        imsi_suffix = payload.get("imsi_start", "895")
        key = defaults["key"]
        op = defaults["op"]
        imsi_base = defaults["imsi_msin_base"]
        supi = f"imsi-{mcc}{mnc}{imsi_base}{imsi_suffix}"

        discovery_script = f"""set -e
DEFAULT_GW=$(ip route | grep -E '^10\\.[0-9]+\\.[0-9]+\\.0/24 dev eth0' | sed 's|.*/24.*||;s|.*\\.||')
ETH0_NET=$(ip route | grep -E '^10\\.[0-9]+\\.[0-9]+\\.0/24 dev eth0' | awk '{{print $1}}' | sed 's|\\.0/24||')
if [ -n "$ETH0_NET" ] && ! ip route | grep -q "^default"; then
  ip route add default via ${{ETH0_NET}}.1 dev eth0 2>/dev/null || true
fi
echo "Discovering gNB {gnb_name} N2 IP..."
for i in $(seq 1 10); do
  RESP=$(curl -sk -H "Authorization: Bearer $DISCOVERY_TOKEN" \
    "${{K8S_API}}/api/v1/namespaces/${{NAMESPACE}}/pods?labelSelector=app={gnb_name}" 2>/dev/null || echo "{{}}")
  GNB_IP=$(echo "$RESP" | jq -r '
    .items[0].metadata.annotations["k8s.v1.cni.cncf.io/network-status"] // empty
    | fromjson | .[] | select(.interface == "n2") | .ips[0] // empty
  ' 2>/dev/null || echo "")
  if [ -n "$GNB_IP" ] && [ "$GNB_IP" != "null" ]; then
    echo "Found gNB N2 IP: $GNB_IP"
    echo "$GNB_IP" > /config/gnb-ip
    exit 0
  fi
  echo "Waiting for gNB... (attempt $i/10)"
  sleep 2
done
echo "ERROR: Could not discover gNB IP"
exit 1"""

        config_script = f"""set -euo pipefail
echo "Waiting for network interface (n2)..."
for i in $(seq 1 30); do
  N2_READY=$(ip addr show n2 2>/dev/null | grep -c "inet " || echo 0)
  if [ "$N2_READY" -gt 0 ]; then break; fi
  sleep 1
done
ORD=${{HOSTNAME##*-}}
GNB_IP=$(cat /UERANSIM/config/gnb-ip)
cat > /UERANSIM/config/ue-${{ORD}}.yaml <<EOF
supi: "{supi}"
mcc: "{mcc}"
mnc: "{mnc}"
protectionScheme: 0
homeNetworkPublicKey: "5a8d38864820197c3394b92613b20b91633cbd897119273bf8e4a6f4eec0a650"
homeNetworkPublicKeyId: 1
routingIndicator: "0000"
key: "{key}"
op: "{op}"
opType: "OP"
amf: "8000"
imei: "356938035643803"
imeiSv: "4370816125816151"
tunNetmask: "255.255.255.0"
gnbSearchList:
  - $GNB_IP
sessions:
  - type: "IPv4"
    apn: "{apn}"
    slice:
      sst: {sst}
      sd: {sd}
configured-nssai:
  - sst: {sst}
    sd: {sd}
default-nssai:
  - sst: {sst}
    sd: {sd}
integrity:
  IA1: true
  IA2: true
  IA3: true
ciphering:
  EA1: true
  EA2: true
  EA3: true
integrityMaxRate:
  uplink: "full"
  downlink: "full"
EOF
echo "UE config generated for {supi}"
cat /UERANSIM/config/ue-${{ORD}}.yaml"""

        return {
            "apiVersion": "apps/v1",
            "kind": "StatefulSet",
            "metadata": {
                "name": name,
                "namespace": NS,
                "labels": {"app": "ue", "component": "ue", "managed-by": "dashboard", "cell-id": str(cell_id), "gnb": gnb_name},
            },
            "spec": {
                "serviceName": name,
                "replicas": replicas,
                "selector": {"matchLabels": {"app": "ue", "ue-id": name}},
                "template": {
                    "metadata": {
                        "labels": {"app": "ue", "component": "ue", "managed-by": "dashboard", "ue-id": name, "cell-id": str(cell_id), "gnb": gnb_name},
                        "annotations": {
                            "k8s.v1.cni.cncf.io/networks": json.dumps([
                                {"name": f"n2-cell-{cell_id}", "interface": "n2"},
                            ]),
                        },
                    },
                    "spec": {
                        "automountServiceAccountToken": False,
                        "nodeSelector": {"kubernetes.io/hostname": node},
                        "volumes": [{"name": "ue-config-runtime", "emptyDir": {}}],
                        "initContainers": [
                            {
                                "name": "gnb-discovery",
                                "image": DISCOVERY_IMAGE,
                                "imagePullPolicy": "IfNotPresent",
                                "securityContext": {"capabilities": {"add": ["NET_ADMIN"]}},
                                "env": [
                                    {"name": "K8S_API", "value": f"https://{api_server}"},
                                    {"name": "NAMESPACE", "value": NS},
                                    {"name": "GNB_NAME", "value": gnb_name},
                                    {"name": "DISCOVERY_TOKEN", "value": token},
                                ],
                                "command": ["/bin/sh", "-c"],
                                "args": [discovery_script],
                                "volumeMounts": [{"name": "ue-config-runtime", "mountPath": "/config"}],
                            },
                            {
                                "name": "config-gen",
                                "image": image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": ["/bin/bash", "-c"],
                                "args": [config_script],
                                "volumeMounts": [{"name": "ue-config-runtime", "mountPath": "/UERANSIM/config"}],
                            },
                        ],
                        "containers": [
                            {
                                "name": "ue",
                                "image": image,
                                "imagePullPolicy": "IfNotPresent",
                                "securityContext": {"privileged": True, "capabilities": {"add": ["NET_ADMIN", "SYS_ADMIN"]}},
                                "command": ["/bin/sh", "-c"],
                                "args": [f"ORD=${{HOSTNAME##*-}}\n{UE_BINARY} -c /UERANSIM/config/ue-${{ORD}}.yaml"],
                                "volumeMounts": [{"name": "ue-config-runtime", "mountPath": "/UERANSIM/config"}],
                                "startupProbe": {"exec": {"command": ["pgrep", "-f", "nr-ue"]}, "initialDelaySeconds": 5, "periodSeconds": 5, "failureThreshold": 60},
                                "livenessProbe": {"exec": {"command": ["pgrep", "-f", "nr-ue"]}, "initialDelaySeconds": 30, "periodSeconds": 10},
                                "resources": payload.get("resources") or {
                                    "requests": {"cpu": "200m", "memory": "256Mi"},
                                    "limits": {"cpu": "500m", "memory": "512Mi"},
                                },
                            }
                        ],
                    },
                },
            },
        }

    # ── CRUD ─────────────────────────────────────────────────────

    def list_gnbs(self) -> list[dict[str, Any]]:
        return self.k8s.list_deployments(NS, label_selector="component=gnb")

    def list_ues(self) -> list[dict[str, Any]]:
        return self.k8s.list_statefulsets(NS, label_selector="app=ue")

    def status(self) -> dict[str, Any]:
        gnbs = self.list_gnbs()
        ues = self.list_ues()
        return {
            "enabled": any(g.get("replicas", 0) > 0 for g in gnbs) or any(u.get("replicas", 0) > 0 for u in ues),
            "gnbs": gnbs,
            "ues": ues,
            "defaults": self._read_topology_defaults(),
        }

    def disable(self) -> dict[str, Any]:
        prev: dict[str, dict[str, int]] = {"gnbs": {}, "ues": {}}
        for g in self.list_gnbs():
            prev["gnbs"][g["name"]] = g.get("replicas", 0)
            self.k8s.scale_deployment(NS, g["name"], 0)
        for u in self.list_ues():
            prev["ues"][u["name"]] = u.get("replicas", 0)
            self.k8s.scale_statefulset(NS, u["name"], 0)
        state = self._get_state()
        state["replicas"] = prev
        self._save_state(state)
        return {"enabled": False, "saved_replicas": prev}

    def enable(self) -> dict[str, Any]:
        prev = self._get_state().get("replicas", {})
        scaled: dict[str, dict[str, int]] = {"gnbs": {}, "ues": {}}
        for g in self.list_gnbs():
            target = int((prev.get("gnbs") or {}).get(g["name"], 1))
            self.k8s.scale_deployment(NS, g["name"], max(1, target))
            scaled["gnbs"][g["name"]] = max(1, target)
        for u in self.list_ues():
            target = int((prev.get("ues") or {}).get(u["name"], 1))
            self.k8s.scale_statefulset(NS, u["name"], max(1, target))
            scaled["ues"][u["name"]] = max(1, target)
        return {"enabled": True, "scaled_to": scaled}

    def scale_gnb(self, name: str, replicas: int) -> dict[str, Any]:
        self.k8s.scale_deployment(NS, name, replicas)
        return self.k8s.rollout_status(NS, "deployment", name)

    def scale_ue(self, name: str, replicas: int) -> dict[str, Any]:
        self.k8s.scale_statefulset(NS, name, replicas)
        return self.k8s.rollout_status(NS, "statefulset", name)

    def activate_gnb(self, name: str) -> dict[str, Any]:
        return self.scale_gnb(name, 1)

    def deactivate_gnb(self, name: str) -> dict[str, Any]:
        return self.scale_gnb(name, 0)

    def activate_ue(self, name: str) -> dict[str, Any]:
        return self.scale_ue(name, 1)

    def deactivate_ue(self, name: str) -> dict[str, Any]:
        return self.scale_ue(name, 0)

    def patch_gnb(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if "replicas" in payload:
            self.k8s.scale_deployment(NS, name, int(payload["replicas"]))
        self.k8s.patch_deployment_template(
            NS, name,
            node_selector=payload.get("nodeSelector"),
            affinity=payload.get("affinity"),
            tolerations=payload.get("tolerations"),
            resources=payload.get("resources"),
        )
        return self.k8s.rollout_status(NS, "deployment", name)

    def patch_ue(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if "replicas" in payload:
            self.k8s.scale_statefulset(NS, name, int(payload["replicas"]))
        self.k8s.patch_statefulset_template(
            NS, name,
            node_selector=payload.get("nodeSelector"),
            affinity=payload.get("affinity"),
            tolerations=payload.get("tolerations"),
            resources=payload.get("resources"),
        )
        return self.k8s.rollout_status(NS, "statefulset", name)

    def delete_gnb(self, name: str) -> dict[str, Any]:
        self.k8s.delete_deployment(NS, name)
        self._delete_form("gnbs", name)
        return {"deleted": name}

    def delete_ue(self, name: str) -> dict[str, Any]:
        self.k8s.delete_statefulset(NS, name)
        self._delete_form("ues", name)
        return {"deleted": name}

    def create_gnb_form(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._validate_gnb_payload(payload)
        name = payload.get("name") or self.get_defaults()["next_gnb_name"]
        full = {**payload, "name": name}
        manifest = self._build_gnb_manifest_from_form(full)
        svc_manifest = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": name,
                "namespace": NS,
                "labels": {"app": name, "cell-id": str(payload.get("cell_id", full.get("cell_id", 1)))},
            },
            "spec": {
                "selector": {"app": name},
                "ports": [
                    {"name": "ngap", "port": 38412, "protocol": "SCTP"},
                    {"name": "gtpu", "port": 2152, "protocol": "UDP"},
                ],
            },
        }
        try:
            self.k8s.core.create_namespaced_service(namespace=NS, body=svc_manifest)
        except ApiException as exc:
            if exc.status != 409:
                log.warning("Service creation failed: %s", exc)
        self.k8s.upsert_deployment(NS, manifest)
        self._record_form("gnbs", name, {k: v for k, v in full.items() if k != "name"})
        return {"created": name}

    def create_ue_form(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._validate_ue_payload(payload)
        name = payload.get("name") or self.get_defaults()["next_ue_name"]
        full = {**payload, "name": name}
        manifest = self._build_ue_manifest_from_form(full)
        self.k8s.upsert_statefulset(NS, manifest)
        self._record_form("ues", name, {k: v for k, v in full.items() if k != "name"})
        return {"created": name}

    def create_gnb(self, payload: dict[str, Any]) -> dict[str, Any]:
        manifest = payload.get("manifest")
        if not isinstance(manifest, dict):
            raise ValueError("manifest is required")
        manifest.setdefault("metadata", {}).setdefault("namespace", NS)
        labels = manifest["metadata"].setdefault("labels", {})
        labels.setdefault("component", "gnb")
        labels.setdefault("managed-by", "dashboard")
        self.k8s.upsert_deployment(NS, manifest)
        return {"created": manifest["metadata"]["name"]}

    def create_ue(self, payload: dict[str, Any]) -> dict[str, Any]:
        manifest = payload.get("manifest")
        if not isinstance(manifest, dict):
            raise ValueError("manifest is required")
        manifest.setdefault("metadata", {}).setdefault("namespace", NS)
        labels = manifest["metadata"].setdefault("labels", {})
        labels.setdefault("component", "ue")
        labels.setdefault("app", "ue")
        labels.setdefault("managed-by", "dashboard")
        self.k8s.upsert_statefulset(NS, manifest)
        return {"created": manifest["metadata"]["name"]}

    def get_rollout(self, kind: str, name: str) -> dict[str, Any]:
        return self.k8s.rollout_status(NS, kind, name)

    def as_mode_status(self) -> dict[str, Any]:
        st = self.status()
        return {
            "enabled": st["enabled"],
            "gnb_count": len(st["gnbs"]),
            "ue_group_count": len(st["ues"]),
            "gnb_replicas": sum(g.get("replicas", 0) for g in st["gnbs"]),
            "ue_replicas": sum(u.get("replicas", 0) for u in st["ues"]),
        }

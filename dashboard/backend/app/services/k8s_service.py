import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from app.config import settings
from app.models import NodeSummary, PodSummary

log = logging.getLogger(__name__)


class K8sService:
    def __init__(self) -> None:
        kubeconfig = os.environ.get("KUBECONFIG", settings.kubeconfig_path)
        config.load_kube_config(config_file=kubeconfig)
        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()
        self.custom = client.CustomObjectsApi()

    def list_nodes(self) -> list[NodeSummary]:
        nodes = self.core.list_node().items
        result: list[NodeSummary] = []
        for n in nodes:
            conditions = n.status.conditions or []
            status = "NotReady"
            for c in conditions:
                if c.type == "Ready" and c.status == "True":
                    status = "Ready"

            roles: list[str] = []
            for label_key in (n.metadata.labels or {}):
                if label_key.startswith("node-role.kubernetes.io/"):
                    roles.append(label_key.split("/", 1)[1])

            addresses = n.status.addresses or []
            ip = None
            for addr in addresses:
                if addr.type == "InternalIP":
                    ip = addr.address
                    break

            result.append(NodeSummary(
                name=n.metadata.name,
                status=status,
                roles=roles,
                ip=ip,
                kubelet_version=n.status.node_info.kubelet_version if n.status.node_info else None,
            ))
        return result

    def list_pods(self, namespace: str) -> list[PodSummary]:
        pods = self.core.list_namespaced_pod(namespace=namespace).items
        return [self._to_summary(p) for p in pods]

    def get_configmap(self, namespace: str, name: str) -> dict[str, Any]:
        cm = self.core.read_namespaced_config_map(name=name, namespace=namespace)
        return {"name": cm.metadata.name, "namespace": cm.metadata.namespace, "data": cm.data or {}}

    def apply_configmap(self, namespace: str, name: str, data: dict[str, str]) -> None:
        body = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(name=name, namespace=namespace),
            data=data,
        )
        try:
            self.core.patch_namespaced_config_map(name=name, namespace=namespace, body=body)
        except ApiException as exc:
            if exc.status != 404:
                raise
            self.core.create_namespaced_config_map(namespace=namespace, body=body)

    def list_deployments(self, namespace: str, label_selector: str = "") -> list[dict[str, Any]]:
        deps = self.apps.list_namespaced_deployment(
            namespace=namespace,
            label_selector=label_selector or None,
        ).items
        out: list[dict[str, Any]] = []
        for dep in deps:
            out.append({
                "name": dep.metadata.name,
                "namespace": dep.metadata.namespace,
                "labels": dep.metadata.labels or {},
                "replicas": dep.spec.replicas or 0,
                "ready_replicas": dep.status.ready_replicas or 0,
                "available_replicas": dep.status.available_replicas or 0,
                "node_selector": dep.spec.template.spec.node_selector or {},
                "affinity": dep.spec.template.spec.affinity.to_dict() if dep.spec.template.spec.affinity else None,
                "tolerations": [t.to_dict() for t in (dep.spec.template.spec.tolerations or [])],
                "containers": [c.name for c in (dep.spec.template.spec.containers or [])],
            })
        return out

    def list_statefulsets(self, namespace: str, label_selector: str = "") -> list[dict[str, Any]]:
        ssets = self.apps.list_namespaced_stateful_set(
            namespace=namespace,
            label_selector=label_selector or None,
        ).items
        out: list[dict[str, Any]] = []
        for st in ssets:
            out.append({
                "name": st.metadata.name,
                "namespace": st.metadata.namespace,
                "labels": st.metadata.labels or {},
                "replicas": st.spec.replicas or 0,
                "ready_replicas": st.status.ready_replicas or 0,
                "node_selector": st.spec.template.spec.node_selector or {},
                "affinity": st.spec.template.spec.affinity.to_dict() if st.spec.template.spec.affinity else None,
                "tolerations": [t.to_dict() for t in (st.spec.template.spec.tolerations or [])],
                "containers": [c.name for c in (st.spec.template.spec.containers or [])],
            })
        return out

    def scale_deployment(self, namespace: str, name: str, replicas: int) -> None:
        patch = {"spec": {"replicas": max(0, int(replicas))}}
        self.apps.patch_namespaced_deployment_scale(name=name, namespace=namespace, body=patch)

    def scale_statefulset(self, namespace: str, name: str, replicas: int) -> None:
        patch = {"spec": {"replicas": max(0, int(replicas))}}
        self.apps.patch_namespaced_stateful_set_scale(name=name, namespace=namespace, body=patch)

    def patch_deployment_template(
        self,
        namespace: str,
        name: str,
        *,
        node_selector: dict[str, str] | None = None,
        affinity: dict[str, Any] | None = None,
        tolerations: list[dict[str, Any]] | None = None,
        resources: dict[str, dict[str, str]] | None = None,
    ) -> None:
        patch: dict[str, Any] = {"spec": {"template": {"spec": {}}}}
        spec = patch["spec"]["template"]["spec"]
        if node_selector is not None:
            spec["nodeSelector"] = node_selector
        if affinity is not None:
            spec["affinity"] = affinity
        if tolerations is not None:
            spec["tolerations"] = tolerations
        if resources is not None:
            dep = self.apps.read_namespaced_deployment(name=name, namespace=namespace)
            containers = dep.spec.template.spec.containers or []
            spec["containers"] = [
                {
                    "name": c.name,
                    "resources": resources if i == 0 else (c.resources.to_dict() if c.resources else {}),
                }
                for i, c in enumerate(containers)
            ]
        self.apps.patch_namespaced_deployment(name=name, namespace=namespace, body=patch)

    def patch_statefulset_template(
        self,
        namespace: str,
        name: str,
        *,
        node_selector: dict[str, str] | None = None,
        affinity: dict[str, Any] | None = None,
        tolerations: list[dict[str, Any]] | None = None,
        resources: dict[str, dict[str, str]] | None = None,
    ) -> None:
        patch: dict[str, Any] = {"spec": {"template": {"spec": {}}}}
        spec = patch["spec"]["template"]["spec"]
        if node_selector is not None:
            spec["nodeSelector"] = node_selector
        if affinity is not None:
            spec["affinity"] = affinity
        if tolerations is not None:
            spec["tolerations"] = tolerations
        if resources is not None:
            st = self.apps.read_namespaced_stateful_set(name=name, namespace=namespace)
            containers = st.spec.template.spec.containers or []
            spec["containers"] = [
                {
                    "name": c.name,
                    "resources": resources if i == 0 else (c.resources.to_dict() if c.resources else {}),
                }
                for i, c in enumerate(containers)
            ]
        self.apps.patch_namespaced_stateful_set(name=name, namespace=namespace, body=patch)

    def upsert_deployment(self, namespace: str, manifest: dict[str, Any]) -> None:
        name = manifest.get("metadata", {}).get("name")
        if not name:
            raise ValueError("Deployment manifest missing metadata.name")
        try:
            self.apps.read_namespaced_deployment(name=name, namespace=namespace)
            self.apps.patch_namespaced_deployment(name=name, namespace=namespace, body=manifest)
        except ApiException as exc:
            if exc.status != 404:
                raise
            self.apps.create_namespaced_deployment(namespace=namespace, body=manifest)

    def upsert_statefulset(self, namespace: str, manifest: dict[str, Any]) -> None:
        name = manifest.get("metadata", {}).get("name")
        if not name:
            raise ValueError("StatefulSet manifest missing metadata.name")
        try:
            self.apps.read_namespaced_stateful_set(name=name, namespace=namespace)
            self.apps.patch_namespaced_stateful_set(name=name, namespace=namespace, body=manifest)
        except ApiException as exc:
            if exc.status != 404:
                raise
            self.apps.create_namespaced_stateful_set(namespace=namespace, body=manifest)

    def delete_deployment(self, namespace: str, name: str) -> None:
        try:
            self.apps.delete_namespaced_deployment(name=name, namespace=namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise

    def delete_statefulset(self, namespace: str, name: str) -> None:
        try:
            self.apps.delete_namespaced_stateful_set(name=name, namespace=namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise

    def rollout_status(self, namespace: str, kind: str, name: str) -> dict[str, Any]:
        if kind == "deployment":
            dep = self.apps.read_namespaced_deployment(name=name, namespace=namespace)
            desired = dep.spec.replicas or 0
            ready = dep.status.ready_replicas or 0
            return {"kind": kind, "name": name, "desired": desired, "ready": ready, "healthy": ready >= desired}
        if kind == "statefulset":
            st = self.apps.read_namespaced_stateful_set(name=name, namespace=namespace)
            desired = st.spec.replicas or 0
            ready = st.status.ready_replicas or 0
            return {"kind": kind, "name": name, "desired": desired, "ready": ready, "healthy": ready >= desired}
        raise ValueError(f"Unsupported rollout kind: {kind}")

    def restart_deployment(self, namespace: str, deployment_name: str) -> None:
        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "dashboard/restarted-at": datetime.now(timezone.utc).isoformat(),
                        }
                    }
                }
            }
        }
        self.apps.patch_namespaced_deployment(name=deployment_name, namespace=namespace, body=patch)

    def pod_logs(self, namespace: str, pod: str, container: str | None = None, tail_lines: int = 500) -> str:
        return self.core.read_namespaced_pod_log(
            name=pod,
            namespace=namespace,
            container=container,
            tail_lines=tail_lines,
            timestamps=True,
        )

    def _to_summary(self, pod: client.V1Pod) -> PodSummary:
        statuses = pod.status.container_statuses or []
        restarts = sum((s.restart_count or 0) for s in statuses)
        containers = [c.name for c in (pod.spec.containers or [])]

        phase = pod.status.phase
        if pod.metadata.deletion_timestamp is not None:
            phase = "Terminating"

        deployment = None
        owners = pod.metadata.owner_references or []
        for owner in owners:
            if owner.kind == "ReplicaSet":
                deployment = owner.name.rsplit("-", 1)[0] if "-" in owner.name else owner.name
                break

        return PodSummary(
            name=pod.metadata.name,
            namespace=pod.metadata.namespace,
            phase=phase,
            restarts=restarts,
            node=pod.spec.node_name,
            pod_ip=pod.status.pod_ip,
            start_time=pod.status.start_time.isoformat() if pod.status.start_time else None,
            deployment=deployment,
            containers=containers,
            labels=pod.metadata.labels or {},
        )

    def list_nads(self, namespace: str) -> list[dict[str, Any]]:
        try:
            result = self.custom.list_namespaced_custom_object(
                group="k8s.cni.cncf.io",
                version="v1",
                namespace=namespace,
                plural="network-attachment-definitions",
            )
        except Exception:
            return []
        nads: list[dict[str, Any]] = []
        for item in result.get("items", []):
            name = item.get("metadata", {}).get("name", "")
            raw_config = item.get("spec", {}).get("config", "{}")
            try:
                cfg = json.loads(raw_config) if isinstance(raw_config, str) else raw_config
            except json.JSONDecodeError:
                cfg = {}
            nads.append({
                "name": name,
                "namespace": item.get("metadata", {}).get("namespace", namespace),
                "type": cfg.get("type", cfg.get("cniVersion", "unknown")),
                "bridge": cfg.get("bridge", ""),
                "master": cfg.get("master", ""),
                "ipam": cfg.get("ipam", {}),
                "raw": cfg,
            })
        return nads

    def list_topology_data(self, namespace: str) -> list[dict[str, Any]]:
        pods = self.core.list_namespaced_pod(namespace=namespace).items
        data: list[dict[str, Any]] = []
        for pod in pods:
            annotation = (pod.metadata.annotations or {}).get("k8s.v1.cni.cncf.io/network-status")
            networks = []
            if annotation:
                try:
                    parsed = json.loads(annotation)
                    if isinstance(parsed, list):
                        networks = parsed
                except json.JSONDecodeError:
                    networks = []

            phase = pod.status.phase
            if pod.metadata.deletion_timestamp is not None:
                phase = "Terminating"

            data.append(
                {
                    "name": pod.metadata.name,
                    "labels": pod.metadata.labels or {},
                    "namespace": pod.metadata.namespace,
                    "phase": phase,
                    "node": pod.spec.node_name,
                    "networks": networks,
                }
            )
        return data


def get_k8s_service() -> K8sService:
    try:
        return K8sService()
    except Exception as exc:
        log.error("Failed to initialise K8sService: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"Kubernetes cluster unreachable: {exc}",
        ) from exc

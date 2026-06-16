import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.stream import stream as _k8s_stream

from app.config import settings
from app.models import NodeSummary, PodSummary

log = logging.getLogger(__name__)


def _parse_cpu_millicores(qty: str) -> float:
    """Convert a Kubernetes CPU quantity string to millicores."""
    qty = qty.strip()
    if qty.endswith("n"):
        return float(qty[:-1]) / 1_000_000
    if qty.endswith("u"):
        return float(qty[:-1]) / 1_000
    if qty.endswith("m"):
        return float(qty[:-1])
    return float(qty) * 1000  # whole cores


def _parse_memory_mb(qty: str) -> float:
    """Convert a Kubernetes memory quantity string to mebibytes."""
    qty = qty.strip()
    if qty.endswith("Ki"):
        return float(qty[:-2]) / 1024
    if qty.endswith("Mi"):
        return float(qty[:-2])
    if qty.endswith("Gi"):
        return float(qty[:-2]) * 1024
    if qty.endswith("Ti"):
        return float(qty[:-2]) * 1024 * 1024
    if qty.endswith("K") or qty.endswith("k"):
        return float(qty[:-1]) * 1e3 / 1024 / 1024
    if qty.endswith("M"):
        return float(qty[:-1]) * 1e6 / 1024 / 1024
    if qty.endswith("G"):
        return float(qty[:-1]) * 1e9 / 1024 / 1024
    return float(qty) / 1024 / 1024  # bare bytes


class K8sService:
    def __init__(self) -> None:
        kubeconfig = os.environ.get("KUBECONFIG", settings.kubeconfig_path)
        config.load_kube_config(config_file=kubeconfig)
        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()
        self.storage = client.StorageV1Api()
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

    # HTTP to an in-cluster Service via the API-server proxy. The backend runs off
    # the cluster (ansible VM) and reaches ClusterIP services only this way, using
    # the kubeconfig it already holds. `name:port` selects the Service port.
    # _preload_content=False returns the raw response: without it the client
    # str()s a JSON body into a Python-repr string (single quotes) that json.loads
    # cannot parse, so always read .data here.
    # Patch a deployment's container image (in-place upgrade); optionally (re)bind
    # envFrom to the given ConfigMap/Secret names (both optional refs). The
    # container name equals the deployment name in every northbound manifest.
    # Strategic-merge leaves inline env and volumes untouched.
    def set_workload_image(self, namespace: str, name: str, image: str,
                           envfrom: list[str] | None = None) -> None:
        container: dict[str, Any] = {"name": name, "image": image}
        if envfrom:
            container["envFrom"] = [{"configMapRef": {"name": envfrom[0], "optional": True}}]
            if len(envfrom) > 1:
                container["envFrom"].append({"secretRef": {"name": envfrom[1], "optional": True}})
        patch = {"spec": {"template": {"spec": {"containers": [container]}}}}
        self.apps.patch_namespaced_deployment(name=name, namespace=namespace, body=patch)

    # Delete specific keys from a ConfigMap / Secret (read-modify-replace, so the
    # keys are actually removed rather than merged). Used to UNSET a config var,
    # e.g. clearing an inline override so a file-backed value takes effect.
    def unset_configmap_keys(self, namespace: str, name: str, keys: list[str]) -> bool:
        try:
            cm = self.core.read_namespaced_config_map(name=name, namespace=namespace)
        except ApiException as exc:
            if exc.status == 404:
                return False
            raise
        data = cm.data or {}
        removed = [k for k in keys if k in data]
        for k in removed:
            del data[k]
        if removed:
            cm.data = data
            self.core.replace_namespaced_config_map(name=name, namespace=namespace, body=cm)
        return bool(removed)

    def unset_secret_keys(self, namespace: str, name: str, keys: list[str]) -> bool:
        try:
            sec = self.core.read_namespaced_secret(name=name, namespace=namespace)
        except ApiException as exc:
            if exc.status == 404:
                return False
            raise
        data = sec.data or {}
        removed = [k for k in keys if k in data]
        for k in removed:
            del data[k]
        if removed:
            sec.data = data
            self.core.replace_namespaced_secret(name=name, namespace=namespace, body=sec)
        return bool(removed)

    def service_proxy_get(self, namespace: str, service: str, port: int, path: str) -> str:
        resp = self.core.connect_get_namespaced_service_proxy_with_path(
            name=f"{service}:{port}", namespace=namespace, path=path, _preload_content=False)
        return resp.data.decode("utf-8")

    def service_proxy_delete(self, namespace: str, service: str, port: int, path: str) -> str:
        resp = self.core.connect_delete_namespaced_service_proxy_with_path(
            name=f"{service}:{port}", namespace=namespace, path=path, _preload_content=False)
        return resp.data.decode("utf-8")

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
            try:
                self.core.create_namespaced_config_map(namespace=namespace, body=body)
            except ApiException as ce:
                # Lost a create race (two near-simultaneous applies): the object
                # now exists, so patch it instead of erroring with 409.
                if ce.status != 409:
                    raise
                self.core.patch_namespaced_config_map(name=name, namespace=namespace, body=body)

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

    def upsert_service(self, namespace: str, manifest: dict[str, Any]) -> None:
        name = manifest.get("metadata", {}).get("name")
        if not name:
            raise ValueError("Service manifest missing metadata.name")
        try:
            self.core.read_namespaced_service(name=name, namespace=namespace)
            self.core.patch_namespaced_service(name=name, namespace=namespace, body=manifest)
        except ApiException as exc:
            if exc.status != 404:
                raise
            self.core.create_namespaced_service(namespace=namespace, body=manifest)

    def delete_service(self, namespace: str, name: str) -> None:
        try:
            self.core.delete_namespaced_service(name=name, namespace=namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise

    def upsert_secret(
        self,
        namespace: str,
        name: str,
        string_data: dict[str, str],
        secret_type: str = "Opaque",
    ) -> None:
        body = client.V1Secret(
            metadata=client.V1ObjectMeta(name=name, namespace=namespace),
            string_data=string_data,
            type=secret_type,
        )
        try:
            self.core.patch_namespaced_secret(name=name, namespace=namespace, body=body)
        except ApiException as exc:
            if exc.status != 404:
                raise
            self.core.create_namespaced_secret(namespace=namespace, body=body)

    def delete_secret(self, namespace: str, name: str) -> None:
        try:
            self.core.delete_namespaced_secret(name=name, namespace=namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise

    def exec_in_pod(self, namespace: str, pod: str, command: list[str], container: str | None = None) -> str | None:
        """Run a command in a pod and return combined stdout (best-effort). Used for
        read-only probes like `cat <path>`; returns None on any failure."""
        try:
            return _k8s_stream(
                self.core.connect_get_namespaced_pod_exec,
                pod, namespace,
                command=command,
                container=container,
                stderr=True, stdin=False, stdout=True, tty=False,
                _preload_content=True, _request_timeout=6,
            )
        except Exception:
            return None

    def delete_configmap(self, namespace: str, name: str) -> None:
        try:
            self.core.delete_namespaced_config_map(name=name, namespace=namespace)
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

    def get_pod_resource_metrics(self, namespace: str) -> dict[str, list]:
        """Query the Kubernetes Metrics API (metrics-server) for current pod CPU/memory.

        Returns the same shape as PrometheusService._extract_vector so the existing
        frontend/router code needs no changes:
          {"cpu": [{"label": pod, "value": millicores}, ...],
           "memory": [{"label": pod, "value": mb}, ...]}
        """
        try:
            result = self.custom.list_namespaced_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                namespace=namespace,
                plural="pods",
            )
        except Exception as exc:
            log.warning("Metrics API unavailable: %s", exc)
            return {"cpu": [], "memory": []}

        cpu_out: list[dict] = []
        mem_out: list[dict] = []
        for item in result.get("items", []):
            pod_name = item.get("metadata", {}).get("name", "")
            containers = item.get("containers", [])
            total_cpu = sum(
                _parse_cpu_millicores(c.get("usage", {}).get("cpu", "0"))
                for c in containers
            )
            total_mem = sum(
                _parse_memory_mb(c.get("usage", {}).get("memory", "0"))
                for c in containers
            )
            cpu_out.append({"label": pod_name, "value": round(total_cpu, 2)})
            mem_out.append({"label": pod_name, "value": round(total_mem, 2)})

        return {"cpu": cpu_out, "memory": mem_out}

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

    # ------------------------------------------------------------------
    # Generic Kubernetes inventory helpers (used by /api/v1/k8s/* router).
    # Kept as lightweight read-only wrappers so the new "Kubernetes" dashboard
    # section does not sprinkle raw client calls across the codebase.
    # ------------------------------------------------------------------

    def list_namespaces(self) -> list[dict[str, Any]]:
        items = self.core.list_namespace().items
        out: list[dict[str, Any]] = []
        for ns in items:
            out.append({
                "name": ns.metadata.name,
                "phase": ns.status.phase if ns.status else None,
                "labels": ns.metadata.labels or {},
                "created": ns.metadata.creation_timestamp.isoformat()
                    if ns.metadata.creation_timestamp else None,
            })
        return out

    def list_pvcs(self, namespace: str | None = None) -> list[dict[str, Any]]:
        if namespace:
            items = self.core.list_namespaced_persistent_volume_claim(namespace=namespace).items
        else:
            items = self.core.list_persistent_volume_claim_for_all_namespaces().items
        out: list[dict[str, Any]] = []
        for pvc in items:
            spec = pvc.spec
            status = pvc.status
            requested = None
            if spec and spec.resources and spec.resources.requests:
                requested = spec.resources.requests.get("storage")
            capacity = None
            if status and status.capacity:
                capacity = status.capacity.get("storage")
            out.append({
                "name": pvc.metadata.name,
                "namespace": pvc.metadata.namespace,
                "phase": status.phase if status else None,
                "volume": spec.volume_name if spec else None,
                "storage_class": spec.storage_class_name if spec else None,
                "access_modes": list(spec.access_modes or []) if spec else [],
                "requested": requested,
                "capacity": capacity,
                "created": pvc.metadata.creation_timestamp.isoformat()
                    if pvc.metadata.creation_timestamp else None,
            })
        return out

    def list_storage_classes(self) -> list[dict[str, Any]]:
        items = self.storage.list_storage_class().items
        out: list[dict[str, Any]] = []
        for sc in items:
            annotations = sc.metadata.annotations or {}
            is_default = annotations.get(
                "storageclass.kubernetes.io/is-default-class"
            ) == "true"
            out.append({
                "name": sc.metadata.name,
                "provisioner": sc.provisioner,
                "reclaim_policy": sc.reclaim_policy,
                "volume_binding_mode": sc.volume_binding_mode,
                "allow_volume_expansion": bool(sc.allow_volume_expansion),
                "is_default": is_default,
                "parameters": sc.parameters or {},
                "created": sc.metadata.creation_timestamp.isoformat()
                    if sc.metadata.creation_timestamp else None,
            })
        return out

    def list_services(self, namespace: str | None = None) -> list[dict[str, Any]]:
        if namespace:
            items = self.core.list_namespaced_service(namespace=namespace).items
        else:
            items = self.core.list_service_for_all_namespaces().items
        out: list[dict[str, Any]] = []
        for svc in items:
            spec = svc.spec
            ports: list[dict[str, Any]] = []
            for p in (spec.ports or []) if spec else []:
                ports.append({
                    "name": p.name,
                    "port": p.port,
                    "target_port": str(p.target_port) if p.target_port is not None else None,
                    "node_port": p.node_port,
                    "protocol": p.protocol,
                })
            out.append({
                "name": svc.metadata.name,
                "namespace": svc.metadata.namespace,
                "type": spec.type if spec else None,
                "cluster_ip": spec.cluster_ip if spec else None,
                "external_ips": list(spec.external_i_ps or []) if spec else [],
                "selector": (spec.selector or {}) if spec else {},
                "ports": ports,
                "created": svc.metadata.creation_timestamp.isoformat()
                    if svc.metadata.creation_timestamp else None,
            })
        return out

    def list_events(
        self,
        namespace: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Recent events ordered newest first. Pass namespace=None for cluster-wide."""
        if namespace:
            items = self.core.list_namespaced_event(namespace=namespace).items
        else:
            items = self.core.list_event_for_all_namespaces().items

        def _ts(ev: Any) -> Any:
            return (
                ev.last_timestamp
                or ev.event_time
                or (ev.metadata.creation_timestamp if ev.metadata else None)
            )

        items = sorted(items, key=lambda e: _ts(e) or 0, reverse=True)[:limit]
        out: list[dict[str, Any]] = []
        for ev in items:
            ts = _ts(ev)
            involved = ev.involved_object
            out.append({
                "namespace": ev.metadata.namespace if ev.metadata else None,
                "type": ev.type,
                "reason": ev.reason,
                "message": ev.message,
                "count": ev.count or 1,
                "component": (ev.source.component if ev.source else None),
                "host": (ev.source.host if ev.source else None),
                "involved": {
                    "kind": involved.kind if involved else None,
                    "name": involved.name if involved else None,
                    "namespace": involved.namespace if involved else None,
                },
                "first_seen": ev.first_timestamp.isoformat() if ev.first_timestamp else None,
                "last_seen": ts.isoformat() if ts else None,
            })
        return out

    def describe_nodes(self) -> list[dict[str, Any]]:
        """Richer node listing with capacity, allocatable, and conditions."""
        nodes = self.core.list_node().items
        out: list[dict[str, Any]] = []
        for n in nodes:
            status = n.status
            conditions = []
            for c in (status.conditions or []):
                conditions.append({
                    "type": c.type,
                    "status": c.status,
                    "reason": c.reason,
                    "message": c.message,
                    "last_transition": c.last_transition_time.isoformat()
                        if c.last_transition_time else None,
                })
            ready = any(
                c["type"] == "Ready" and c["status"] == "True"
                for c in conditions
            )
            roles: list[str] = []
            for label_key in (n.metadata.labels or {}):
                if label_key.startswith("node-role.kubernetes.io/"):
                    roles.append(label_key.split("/", 1)[1])
            internal_ip = None
            for addr in (status.addresses or []):
                if addr.type == "InternalIP":
                    internal_ip = addr.address
                    break
            info = status.node_info
            out.append({
                "name": n.metadata.name,
                "ready": ready,
                "roles": roles,
                "internal_ip": internal_ip,
                "kubelet_version": info.kubelet_version if info else None,
                "os_image": info.os_image if info else None,
                "kernel": info.kernel_version if info else None,
                "container_runtime": info.container_runtime_version if info else None,
                "capacity": dict(status.capacity or {}),
                "allocatable": dict(status.allocatable or {}),
                "conditions": conditions,
                "taints": [
                    {"key": t.key, "value": t.value, "effect": t.effect}
                    for t in ((n.spec.taints if n.spec else None) or [])
                ],
                "created": n.metadata.creation_timestamp.isoformat()
                    if n.metadata.creation_timestamp else None,
            })
        return out


def get_k8s_service() -> K8sService:
    try:
        return K8sService()
    except Exception as exc:
        log.error("Failed to initialise K8sService: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"Kubernetes cluster unreachable: {exc}",
        ) from exc

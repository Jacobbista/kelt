# utils/k8s_client.py
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
import base64
import os
import subprocess

from kubernetes import client, config
from kubernetes.stream import stream
from kubernetes.client import ApiException


class K8sClientError(Exception):
    pass


@dataclass
class ExecResult:
    """Result of exec_in_pod, compatible with subprocess.CompletedProcess"""
    stdout: str
    stderr: str = ""
    returncode: int = 0
    
    def __bool__(self):
        return self.returncode == 0


class K8sClient:
    """
    Thin wrapper over kubernetes Python client.
    Uses only API calls; no subprocess/kubectl.
    """

    def __init__(self, kubeconfig_path: Optional[str] = None, context: Optional[str] = None):
        kubeconfig_path = kubeconfig_path or os.environ.get("KUBECONFIG")
        # The configured path is often a VM path (/home/vagrant/kubeconfig) that
        # does not exist on a host run. Fall back to the KUBECONFIG env the test
        # runner fetches into tests/ so suites work from the host too.
        if not (kubeconfig_path and os.path.exists(kubeconfig_path)):
            env_kc = os.environ.get("KUBECONFIG")
            if env_kc and os.path.exists(env_kc):
                kubeconfig_path = env_kc
        if kubeconfig_path and os.path.exists(kubeconfig_path):
            config.load_kube_config(config_file=kubeconfig_path, context=context)
        else:
            # Fallback for in-cluster (not typical here, but harmless)
            try:
                config.load_incluster_config()
            except Exception as e:
                raise K8sClientError(
                    f"Cannot load kubeconfig at '{kubeconfig_path}' and not in cluster: {e}"
                )
        self.core = client.CoreV1Api()
        self.custom = client.CustomObjectsApi()

    # ---------- Core getters ----------

    def get_nodes(self) -> List[Dict[str, Any]]:
        return self.core.list_node().to_dict().get("items", [])

    def get_pods(self, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        ns = namespace or ""
        if ns:
            pods = self.core.list_namespaced_pod(ns)
        else:
            pods = self.core.list_pod_for_all_namespaces()
        return pods.to_dict().get("items", [])

    def get_services(self, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        ns = namespace or ""
        if ns:
            svcs = self.core.list_namespaced_service(ns)
        else:
            svcs = self.core.list_service_for_all_namespaces()
        return svcs.to_dict().get("items", [])

    def get_network_attachments(self, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        # Multus CRD: k8s.cni.cncf.io/v1 NetworkAttachmentDefinition
        ns = namespace or ""
        group = "k8s.cni.cncf.io"
        version = "v1"
        plural = "network-attachment-definitions"
        if ns:
            obj = self.custom.list_namespaced_custom_object(group, version, ns, plural)
        else:
            obj = self.custom.list_cluster_custom_object(group, version, plural)
        return obj.get("items", [])

    # ---------- Logs / Events ----------

    def get_pod_logs(
        self,
        pod_name: str,
        namespace: str,
        container: Optional[str] = None,
        tail_lines: int = 200,
    ) -> str:
        try:
            return self.core.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container=container,
                tail_lines=tail_lines,
                timestamps=False,
            )
        except ApiException as e:
            raise K8sClientError(f"read log failed: {e}")

    def get_pod_events(self, pod_name: str, namespace: str) -> List[Dict[str, Any]]:
        # Filter events by field selector for this pod
        try:
            field_selector = f"involvedObject.kind=Pod,involvedObject.name={pod_name},involvedObject.namespace={namespace}"
            ev = self.core.list_namespaced_event(
                namespace=namespace, field_selector=field_selector
            )
            return ev.to_dict().get("items", [])
        except ApiException as e:
            raise K8sClientError(f"list events failed: {e}")

    # ---------- Exec ----------

    def _pick_default_container(self, pod: Dict[str, Any]) -> Optional[str]:
        spec = pod.get("spec") or {}
        containers = spec.get("containers") or []
        return containers[0]["name"] if containers else None

    def _get_pod(self, pod_name: str, namespace: str) -> Dict[str, Any]:
        try:
            pod = self.core.read_namespaced_pod(pod_name, namespace)
            return pod.to_dict()
        except ApiException as e:
            raise K8sClientError(f"get pod failed: {e}")

    def exec_in_pod(
        self,
        pod_name: str,
        namespace: str,
        command: List[str],
        container: Optional[str] = None,
        tty: bool = False,
        timeout: int = 60,
        retry_if_not_found: bool = True,
    ) -> ExecResult:
        """
        Executes a command inside a pod.
        - If `container` is None, Kubernetes may still require it when multiple containers exist.
        - On 'container not found' errors, it retries automatically with the first non-init container.
        Returns ExecResult with stdout, stderr, returncode.
        """
        try:
            output = stream(
                self.core.connect_get_namespaced_pod_exec,
                name=pod_name,
                namespace=namespace,
                container=container,
                command=command,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=tty,
                _request_timeout=timeout,
            )
            return ExecResult(stdout=output, stderr="", returncode=0)
        except ApiException as e:
            msg = getattr(e, "body", "") or str(e)
            needs_retry = retry_if_not_found and ("container not found" in msg.lower())
            if needs_retry:
                pod = self._get_pod(pod_name, namespace)
                fallback = self._pick_default_container(pod)
                if fallback and fallback != container:
                    try:
                        output = stream(
                            self.core.connect_get_namespaced_pod_exec,
                            name=pod_name,
                            namespace=namespace,
                            container=fallback,
                            command=command,
                            stderr=True,
                            stdin=False,
                            stdout=True,
                            tty=tty,
                            _request_timeout=timeout,
                        )
                        return ExecResult(stdout=output, stderr="", returncode=0)
                    except ApiException as e2:
                        return ExecResult(stdout="", stderr=str(e2), returncode=1)
            return ExecResult(stdout="", stderr=msg, returncode=1)
        except Exception as e:
            return ExecResult(stdout="", stderr=str(e), returncode=1)

    # ---------- kubectl-like commands ----------

    def run_command(self, args: List[str], namespace: Optional[str] = None) -> ExecResult:
        """
        Run kubectl command via subprocess.
        Used for operations like delete, rollout restart, etc.
        """
        kubeconfig = os.environ.get("KUBECONFIG", "")
        cmd = ["kubectl"]
        if kubeconfig:
            cmd.extend(["--kubeconfig", kubeconfig])
        if namespace:
            cmd.extend(["-n", namespace])
        cmd.extend(args)
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            return ExecResult(
                stdout=result.stdout,
                stderr=result.stderr,
                returncode=result.returncode
            )
        except subprocess.TimeoutExpired:
            return ExecResult(stdout="", stderr="Command timed out", returncode=124)
        except FileNotFoundError:
            return ExecResult(stdout="", stderr="kubectl not found", returncode=127)
        except Exception as e:
            return ExecResult(stdout="", stderr=str(e), returncode=1)

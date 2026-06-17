from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DASHBOARD_", env_file=".env", extra="ignore")

    app_name: str = "5G Dashboard API"
    app_env: str = "dev"
    mode: str = "prod"
    runtime_source: str = "unknown"
    host: str = "0.0.0.0"
    port: int = 8080

    kubeconfig_path: str = "/vagrant/tests/kubeconfig"
    default_namespace: str = "5g"

    worker_ssh_host: str = "worker"
    shell_timeout_seconds: int = 10
    shell_max_output_bytes: int = 1_000_000

    prometheus_url: str = "http://192.168.56.11:30090"
    mongodb_url: str = "mongodb://192.168.56.11:30017/open5gs"

    admin_token: str = "change-me"
    allow_configmap_write: bool = False
    # Gate for the Northbound console's deploy-from-image endpoint, which creates
    # arbitrary Deployments/Services/Secrets in the northbound namespaces. Off by
    # default (read-only console); phase 09 sets DASHBOARD_ALLOW_WORKLOAD_CREATE=true
    # to enable it. Always admin-gated on top of this. See docs/security/iam.md.
    allow_workload_create: bool = False
    # Edge apps platform (phase 12). The namespace the Apps console deploys into,
    # and the external base domain/scheme used to derive each app's public URL
    # (<name>.<base>). Empty base => no public URL is advertised (LAN-only).
    apps_namespace: str = "apps"
    # Registry host as it appears in image tags (<host>/<name>:<tag>); shown in the
    # Apps page so the operator knows where to docker push. Empty if the platform
    # is not configured. Mirrors all.yml apps_registry_host.
    apps_registry_host: str = ""
    # Registry basic-auth, surfaced to admins in the Apps page (show/hide) so they
    # can docker login + push. Admin-gated endpoint only.
    apps_registry_username: str = ""
    apps_registry_password: str = ""
    external_base_domain: str = ""
    external_scheme: str = "https"
    backend_service_name: str = "dashboard-backend"
    frontend_service_name: str = "dashboard-frontend"
    # Optional external URL the dev frontend is reachable at. Used by the
    # prod UI to render an "Open dev" link. Empty leaves the link to
    # window.__ENV__.DASHBOARD_DEV_EXTERNAL_URL on the browser side.
    dev_external_url: str = ""

    audit_log_path: str = "logs/audit.log"
    cors_origin: str = "http://localhost:5173"

    # Subscriber snapshot ConfigMap (see docs/architecture/subscriber-persistence.md)
    # Kept in sync with MongoDB on every subscriber change so the MongoDB pod can
    # re-seed itself from the latest state on restart.
    subscriber_snapshot_namespace: str = "5g"
    subscriber_snapshot_configmap: str = "subscribers-snapshot"
    subscriber_snapshot_key: str = "snapshot.json"

    # ── Keycloak / OIDC ────────────────────────────────────────────────
    # Set skip_auth=True until phase 08 (IAM) is deployed and the realm is
    # reachable. When True, requests bypass JWT validation and behave as
    # dashboard-admin. See docs/security/iam.md.
    skip_auth: bool = True
    keycloak_url: str = "http://keycloak.iam.svc.cluster.local:8080"
    keycloak_realm: str = "5g-testbed"
    # Client IDs accepted in the JWT "azp" / "aud" claims. The browser
    # dashboard issues tokens for "dashboard"; the read-only service
    # account issues tokens for "dashboard-readonly".
    keycloak_accepted_clients: str = "dashboard,dashboard-readonly"
    # Optional path prefix when Keycloak is served under a reverse proxy
    # (for example "/auth"). Leave empty for root-path deploys.
    keycloak_path_prefix: str = ""

    def ensure_audit_dir(self) -> None:
        Path(self.audit_log_path).parent.mkdir(parents=True, exist_ok=True)


settings = Settings()

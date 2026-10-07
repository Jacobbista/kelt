"""Client-secret reveal and rotation for the seeded M2M clients.

The file .testbed.secrets on the host is the single source of truth for these
secrets. A rotation therefore writes the FILE FIRST, then converges Keycloak
(and the one pod that consumes its secret) to it. If the converge step dies
half-way the state is "file new, Keycloak old", which is exactly what the
phase-08 align tasks repair: recovery is pressing rotate again or running
`kelt run-phase 08-iam`, never an ambiguous split. See docs/security/iam.md.
"""

import os
import secrets as pysecrets
import tempfile
from typing import Any

import httpx

from app.config import settings
from app.services.k8s_service import K8sService
from app.services.northbound_service import NorthboundService, TESTBED_SECRETS

# Allowlist: only the seeded M2M clients, each mapped to its .testbed.secrets
# key and (when one exists) the in-cluster workload that presents the secret
# and must be patched along with Keycloak. User passwords are deliberately NOT
# here: users reset their own password in Keycloak, a client secret has no
# self-service path. placement-editor-proxy is also excluded on purpose - it is
# infrastructure plumbing rotated via the file + phase 08, not a consumer
# credential (see docs/security/iam.md "Secret rotation").
CLIENTS = {
    "camara-gateway": {
        "env_key": "CAMARA_CLIENT_SECRET",
        # namespace, deployment, container, env var the pod presents
        "consumer": ("camara", "camara-gateway", "camara-gateway", "CAMARA_CLIENT_SECRET"),
    },
    "camara-api-demo": {"env_key": "CAMARA_API_DEMO_SECRET", "consumer": None},
    "dashboard-readonly": {"env_key": "DASHBOARD_READONLY_SECRET", "consumer": None},
}


class IamService:
    def __init__(self, k8s: K8sService | None = None):
        self.k8s = k8s

    # ── secrets file ───────────────────────────────────────────────────────────
    @staticmethod
    def read_secret(env_key: str) -> str | None:
        return NorthboundService._source_env_file(TESTBED_SECRETS).get(env_key)

    @staticmethod
    def _write_secret(env_key: str, value: str) -> None:
        """Replace (or append) one KEY=value line, leaving every other line
        byte-for-byte intact, atomically and keeping 0600. The version it
        replaces is kept as .testbed.secrets.prev (kelt restore secrets)."""
        lines: list[str] = []
        if TESTBED_SECRETS.exists():
            lines = TESTBED_SECRETS.read_text().splitlines()
        replaced = False
        for i, line in enumerate(lines):
            if line.split("=", 1)[0].strip() == env_key and "=" in line:
                lines[i] = f"{env_key}={value}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{env_key}={value}")
        fd, tmp = tempfile.mkstemp(dir=str(TESTBED_SECRETS.parent), prefix=".testbed.secrets.")
        try:
            with os.fdopen(fd, "w") as f:
                f.write("\n".join(lines) + "\n")
            os.chmod(tmp, 0o600)
            if TESTBED_SECRETS.exists():
                prev = TESTBED_SECRETS.with_name(TESTBED_SECRETS.name + ".prev")
                # Created 0600 from the start: never readable by others, even briefly.
                pfd = os.open(tmp + ".prev", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(pfd, "wb") as f:
                    f.write(TESTBED_SECRETS.read_bytes())
                os.replace(tmp + ".prev", prev)
            os.replace(tmp, TESTBED_SECRETS)
        except BaseException:
            for leftover in (tmp, tmp + ".prev"):
                try:
                    os.unlink(leftover)
                except OSError:
                    pass
            raise

    # ── Keycloak admin API ─────────────────────────────────────────────────────
    @staticmethod
    def _kc_base() -> str:
        return f"{settings.keycloak_url.rstrip('/')}{settings.keycloak_path_prefix}"

    def _admin_token(self) -> str:
        password = self.read_secret("KEYCLOAK_ADMIN_PASSWORD")
        if not password:
            raise RuntimeError("KEYCLOAK_ADMIN_PASSWORD is not in .testbed.secrets on the host")
        resp = httpx.post(
            f"{self._kc_base()}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": settings.keycloak_admin_user,
                "password": password,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def _set_client_secret(self, client_id: str, new_secret: str) -> None:
        token = self._admin_token()
        headers = {"Authorization": f"Bearer {token}"}
        realm = settings.keycloak_realm
        base = f"{self._kc_base()}/admin/realms/{realm}"
        found = httpx.get(f"{base}/clients", params={"clientId": client_id}, headers=headers, timeout=10)
        found.raise_for_status()
        reps = found.json()
        if not reps:
            raise RuntimeError(f"client '{client_id}' does not exist in realm {realm}")
        rep = reps[0]
        rep["secret"] = new_secret
        put = httpx.put(f"{base}/clients/{rep['id']}", json=rep, headers=headers, timeout=10)
        put.raise_for_status()

    # ── audit ──────────────────────────────────────────────────────────────────
    def audit_events(self, date_from: str, max_events: int = 1000) -> dict[str, Any]:
        """Keycloak's stored user and admin events since date_from (YYYY-MM-DD),
        and the user names behind their ids (admin events are made by users of
        the master realm, sign-ins by users of the dashboard realm)."""
        headers = {"Authorization": f"Bearer {self._admin_token()}"}
        base = f"{self._kc_base()}/admin/realms"
        realm = settings.keycloak_realm

        def get(path: str, **params: Any) -> Any:
            r = httpx.get(f"{base}/{path}", params=params, headers=headers, timeout=10)
            r.raise_for_status()
            return r.json()

        users: dict[str, str] = {}
        for r in (realm, "master"):
            for u in get(f"{r}/users", max=1000, briefRepresentation="true"):
                users[u["id"]] = u.get("username") or u["id"]
        return {
            "user_events": get(f"{realm}/events", dateFrom=date_from, max=max_events),
            "admin_events": get(f"{realm}/admin-events", dateFrom=date_from, max=max_events),
            "users": users,
        }

    # ── rotation ───────────────────────────────────────────────────────────────
    def rotate(self, client_id: str) -> dict[str, Any]:
        meta = CLIENTS[client_id]
        new_secret = pysecrets.token_urlsafe(24)
        # File first: it is the source of truth, and phase 08 can always
        # re-converge Keycloak to it if anything below fails.
        self._write_secret(meta["env_key"], new_secret)
        self._set_client_secret(client_id, new_secret)
        pod_synced = False
        if meta["consumer"] and self.k8s:
            ns, deploy, container, env_name = meta["consumer"]
            # Strategic merge patch: env entries merge by name, and the pod
            # template change triggers the rollout that re-presents the secret.
            self.k8s.apps.patch_namespaced_deployment(
                name=deploy,
                namespace=ns,
                body={"spec": {"template": {"spec": {"containers": [
                    {"name": container, "env": [{"name": env_name, "value": new_secret}]}
                ]}}}},
            )
            pod_synced = True
        return {"client_id": client_id, "secret": new_secret, "pod_synced": pod_synced}

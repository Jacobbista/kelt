"""
IAM Tests for K3s KubeEdge Testbed (phase 08).

Validates the deployed Keycloak realm rather than application logic:
the OIDC discovery document, the master admin credential, the imported
realm topology (clients + roles), and a CAMARA gateway service-account
token. Mirrors the manual curl checks documented in docs/security/iam.md.

Application-level auth logic (JWT decode, 401 on bad token) belongs in
each downstream service's own repository test suite, where it can run in
cloud CI without a live cluster.
"""
import sys
import os
import base64
import json
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from utils.k8s_client import K8sClient
from utils.test_helpers import TestConfig, TestLogger


# Secrets are never stored in the repo. They are read from the environment
# (KEYCLOAK_ADMIN_PASSWORD, CAMARA_CLIENT_SECRET) and, as a convenience for
# local runs, from the project-root .testbed.secrets file the testbed-config
# tool writes. Tests that need an absent secret are skipped, not failed.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRETS_FILE = PROJECT_ROOT / ".testbed.secrets"


def _decode_jwt_payload(token: str) -> dict:
    """Decode the (unverified) payload segment of a JWT."""
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


class IamTestSuite:
    """Keycloak realm validation suite."""

    def __init__(self, verbose: bool = False):
        self.config = TestConfig()
        self.logger = TestLogger(verbose)
        # The configured path is a VM path; on a host run it does not exist, so
        # fall back to the KUBECONFIG env the runner fetches into tests/.
        kubeconfig = self.config.get("cluster.kubeconfig_path")
        if not (kubeconfig and os.path.exists(kubeconfig)):
            kubeconfig = None
        self.kubectl = K8sClient(kubeconfig)
        self.verbose = verbose

        host = self.config.get("iam.keycloak_host") or self.config.get("cluster.worker_host")
        port = self.config.get("iam.keycloak_nodeport", 31910)
        prefix = self.config.get("iam.keycloak_path_prefix", "")
        self.base_url = f"http://{host}:{port}{prefix}"
        self.realm = self.config.get("iam.realm", "5g-testbed")
        self.namespace = self.config.get("iam.namespace", "iam")
        self.admin_user = self.config.get("iam.admin_user", "admin")
        self.expected_clients = self.config.get("iam.expected_clients", [])
        self.expected_roles = self.config.get("iam.expected_realm_roles", [])
        self.timeout = self.config.get("iam.request_timeout", 15)

    # ── Secret resolution ───────────────────────────────────────────────
    def _get_secret(self, env_key: str) -> str:
        val = os.environ.get(env_key)
        if val:
            return val
        if SECRETS_FILE.exists():
            for line in SECRETS_FILE.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() == env_key:
                    return value.strip()
        return ""

    def _admin_token(self) -> str:
        """Obtain a master-realm admin token, or '' if the password is absent."""
        password = self._get_secret("KEYCLOAK_ADMIN_PASSWORD")
        if not password:
            return ""
        # requests form-encodes data=, so a base64 secret with '+' or '/' is
        # url-encoded correctly. See docs/security/iam.md (M2M token retrieval).
        resp = requests.post(
            f"{self.base_url}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": self.admin_user,
                "password": password,
            },
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            return ""
        return resp.json().get("access_token", "")

    # ── Tests ───────────────────────────────────────────────────────────
    def run_all_tests(self) -> bool:
        self.logger.info(f"Starting IAM Test Suite against {self.base_url}")

        tests = [
            ("Keycloak pods running", self.test_keycloak_pods_running),
            ("OIDC discovery document", self.test_openid_discovery),
            ("Master admin credential", self.test_admin_token),
            ("Realm clients present", self.test_clients_present),
            ("Realm roles present", self.test_realm_roles_present),
            ("CAMARA client_credentials token", self.test_camara_client_credentials),
            ("Per-consumer org claim (camara-consumer-demo)", self.test_consumer_demo_org_claim),
        ]

        passed = 0
        failed = 0
        for test_name, test_func in tests:
            self.logger.test_start(test_name)
            try:
                success = test_func()
                passed += 1 if success else 0
                failed += 0 if success else 1
            except Exception as e:
                self.logger.error(f"{test_name} failed with exception: {e}")
                success = False
                failed += 1
            self.logger.test_end(test_name, success)

        self.logger.info(f"IAM Test Results: {passed} passed, {failed} failed")
        return failed == 0

    def test_keycloak_pods_running(self) -> bool:
        """Keycloak and PostgreSQL pods are Running in the iam namespace."""
        try:
            pods = self.kubectl.get_pods(self.namespace)
        except Exception as e:
            self.logger.error(f"Could not list pods in '{self.namespace}': {e}")
            return False

        wanted = ["keycloak", "keycloak-db"]
        for prefix in wanted:
            matches = [p for p in pods if p["metadata"]["name"].startswith(prefix)]
            running = [p for p in matches if p.get("status", {}).get("phase") == "Running"]
            if not running:
                self.logger.error(f"No Running pod found for '{prefix}' in '{self.namespace}'")
                return False
            self.logger.success(f"{prefix} Running ({running[0]['metadata']['name']})")
        return True

    def test_openid_discovery(self) -> bool:
        """OIDC discovery document is reachable and well-formed."""
        url = f"{self.base_url}/realms/{self.realm}/.well-known/openid-configuration"
        try:
            resp = requests.get(url, timeout=self.timeout)
        except Exception as e:
            self.logger.error(f"Discovery request failed: {e}")
            return False

        if resp.status_code != 200:
            self.logger.error(f"Discovery returned HTTP {resp.status_code} (path prefix wrong?)")
            self.logger.info(f"[debug] GET {url}\n{resp.text[:500]}")
            return False

        doc = resp.json()
        for key in ("issuer", "token_endpoint", "jwks_uri"):
            if not doc.get(key):
                self.logger.error(f"Discovery document missing '{key}'")
                return False
        self.logger.success(f"Discovery OK, issuer={doc['issuer']}")
        self.logger.info(f"[debug] token_endpoint={doc['token_endpoint']}")
        self.logger.info(f"[debug] jwks_uri={doc['jwks_uri']}")
        return True

    def test_admin_token(self) -> bool:
        """Master-realm admin login succeeds with the configured password."""
        if not self._get_secret("KEYCLOAK_ADMIN_PASSWORD"):
            self.logger.warning("SKIP: KEYCLOAK_ADMIN_PASSWORD not in env or .testbed.secrets")
            return True
        token = self._admin_token()
        if not token:
            self.logger.error("Admin token grant failed (wrong password? import-once mismatch?)")
            self.logger.info("See docs/security/iam.md#admin-password-is-import-once")
            return False
        self.logger.success("Master admin token obtained")
        return True

    def test_clients_present(self) -> bool:
        """All expected clients exist in the realm."""
        token = self._admin_token()
        if not token:
            self.logger.warning("SKIP: no admin token (KEYCLOAK_ADMIN_PASSWORD absent)")
            return True
        if not self.expected_clients:
            self.logger.warning("SKIP: no expected_clients configured")
            return True

        resp = requests.get(
            f"{self.base_url}/admin/realms/{self.realm}/clients",
            headers={"Authorization": f"Bearer {token}"},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            self.logger.error(f"Client list returned HTTP {resp.status_code}")
            self.logger.info(f"[debug] {resp.text[:500]}")
            return False

        found = {c["clientId"] for c in resp.json()}
        self.logger.info(f"[debug] clients in realm: {sorted(found)}")
        missing = [c for c in self.expected_clients if c not in found]
        if missing:
            self.logger.error(f"Missing clients: {missing}")
            return False
        self.logger.success(f"All expected clients present: {self.expected_clients}")
        return True

    def test_realm_roles_present(self) -> bool:
        """All expected realm roles exist."""
        token = self._admin_token()
        if not token:
            self.logger.warning("SKIP: no admin token (KEYCLOAK_ADMIN_PASSWORD absent)")
            return True
        if not self.expected_roles:
            self.logger.warning("SKIP: no expected_realm_roles configured")
            return True

        resp = requests.get(
            f"{self.base_url}/admin/realms/{self.realm}/roles",
            headers={"Authorization": f"Bearer {token}"},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            self.logger.error(f"Role list returned HTTP {resp.status_code}")
            self.logger.info(f"[debug] {resp.text[:500]}")
            return False

        found = {r["name"] for r in resp.json()}
        self.logger.info(f"[debug] realm roles: {sorted(found)}")
        missing = [r for r in self.expected_roles if r not in found]
        if missing:
            self.logger.error(f"Missing realm roles: {missing}")
            return False
        self.logger.success(f"All expected realm roles present: {self.expected_roles}")
        return True

    def test_camara_client_credentials(self) -> bool:
        """CAMARA gateway service account gets a token carrying camara-location-read."""
        secret = self._get_secret("CAMARA_CLIENT_SECRET")
        if not secret:
            self.logger.warning("SKIP: CAMARA_CLIENT_SECRET not in env or .testbed.secrets")
            return True

        resp = requests.post(
            f"{self.base_url}/realms/{self.realm}/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "camara-gateway",
                "client_secret": secret,
            },
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            self.logger.error(f"client_credentials grant returned HTTP {resp.status_code}: {resp.text}")
            return False

        body = resp.json()
        token = body.get("access_token", "")
        if not token:
            self.logger.error("Grant succeeded but no access_token in response")
            return False

        # Log non-sensitive claims only; never log the bearer token itself.
        claims = _decode_jwt_payload(token)
        roles = claims.get("realm_access", {}).get("roles", [])
        self.logger.info(f"[debug] azp={claims.get('azp')} expires_in={body.get('expires_in')}")
        self.logger.info(f"[debug] realm_access.roles={roles}")
        if "camara-location-read" not in roles:
            self.logger.error(f"Token missing camara-location-read role. Got: {roles}")
            return False
        # The gateway client is the deliberate org-less operator bypass (a token with
        # no org claim sees every org's assets); per-consumer clients carry an org
        # claim instead. If an org mapper leaks onto this client the bypass breaks.
        if claims.get("org") is not None:
            self.logger.error(f"camara-gateway must stay org-less (operator bypass); got org={claims.get('org')!r}")
            return False
        self.logger.success("CAMARA token carries camara-location-read (org-less operator bypass)")
        return True

    def test_consumer_demo_org_claim(self) -> bool:
        """The reference per-consumer client (camara-consumer-demo) carries its tenant
        org claim (org=demo) plus camara-location-read, so the gateway's 2-legged org
        join scopes it to its tenant's assets. Falls back to the role-default secret
        ('changeme-consumer') when no CAMARA_CONSUMER_DEMO_SECRET override is set, so
        the test runs on a clean deploy."""
        secret = self._get_secret("CAMARA_CONSUMER_DEMO_SECRET") or "changeme-consumer"
        resp = requests.post(
            f"{self.base_url}/realms/{self.realm}/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "camara-consumer-demo",
                "client_secret": secret,
            },
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            self.logger.error(f"consumer-demo grant returned HTTP {resp.status_code}: {resp.text}")
            return False
        token = resp.json().get("access_token", "")
        if not token:
            self.logger.error("Grant succeeded but no access_token in response")
            return False
        claims = _decode_jwt_payload(token)
        org = claims.get("org")
        roles = claims.get("realm_access", {}).get("roles", [])
        self.logger.info(f"[debug] org={org} roles={roles}")
        if org != "demo":
            self.logger.error(f"Token missing org=demo claim (the hardcoded org mapper). Got org={org!r}")
            return False
        if "camara-location-read" not in roles:
            self.logger.error(f"Token missing camara-location-read role. Got: {roles}")
            return False
        self.logger.success("consumer-demo token carries org=demo + camara-location-read")
        return True


def main():
    import argparse

    parser = argparse.ArgumentParser(description="IAM (Keycloak) Tests")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    test_suite = IamTestSuite(verbose=args.verbose)
    success = test_suite.run_all_tests()

    if success:
        print("\n🎉 All IAM tests passed!")
        sys.exit(0)
    else:
        print("\n💥 Some IAM tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()

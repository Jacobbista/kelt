"""
CAMARA / Northbound e2e tests (phase 10).

Validates the deployed CAMARA Location gateway and the positioning chain, not
just the realm: gateway health, auth gating, capabilities aggregation,
location-retrieval by assetId (the private-asset profile), the Asset Identity
Map (GET /assets), and the 2-legged per-org isolation join. Codifies the manual
curl checks; mirrors iam/test_iam.py.

Tokens use client_credentials against Keycloak (same realm as the iam suite).
Northbound is an opt-in feature: if the gateway is unreachable the whole suite
skips (not fails). Tests needing a secret absent from env / .testbed.secrets are
skipped, not failed.
"""
import sys
import os
import base64
import json
import copy
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from utils.test_helpers import TestConfig, TestLogger

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRETS_FILE = PROJECT_ROOT / ".testbed.secrets"


def _decode_jwt_payload(token: str) -> dict:
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


class CamaraTestSuite:
    """CAMARA gateway + positioning + per-org isolation validation suite."""

    def __init__(self, verbose: bool = False):
        self.config = TestConfig()
        self.logger = TestLogger(verbose)
        self.verbose = verbose

        # Keycloak token endpoint — reuse the iam block so realm/host live once.
        kc_host = self.config.get("iam.keycloak_host") or self.config.get("cluster.worker_host")
        kc_port = self.config.get("iam.keycloak_nodeport", 31910)
        kc_prefix = self.config.get("iam.keycloak_path_prefix", "")
        self.realm = self.config.get("iam.realm", "5g-testbed")
        self.token_url = f"http://{kc_host}:{kc_port}{kc_prefix}/realms/{self.realm}/protocol/openid-connect/token"

        # CAMARA gateway (worker NodePort).
        gw_host = self.config.get("camara.gateway_host") or self.config.get("cluster.worker_host")
        gw_port = self.config.get("camara.gateway_nodeport", 31920)
        self.gw = f"http://{gw_host}:{gw_port}"
        self.timeout = self.config.get("camara.request_timeout", 15)
        self.demo_asset_id = self.config.get("camara.demo_asset_id", "demo-asset-01")

    # ── helpers ──────────────────────────────────────────────────────────
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

    def _token(self, client_id: str, secret: str) -> str:
        resp = requests.post(
            self.token_url,
            data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            self.logger.error(f"token grant for {client_id} returned HTTP {resp.status_code}: {resp.text}")
            return ""
        return resp.json().get("access_token", "")

    def _operator_token(self) -> str:
        """Org-less operator token (camara-gateway client) that sees every org."""
        secret = self._get_secret("CAMARA_CLIENT_SECRET")
        return self._token("camara-gateway", secret) if secret else ""

    def _consumer_token(self) -> str:
        """org=demo per-consumer token (camara-api-demo)."""
        secret = self._get_secret("CAMARA_API_DEMO_SECRET") or "changeme-consumer"
        return self._token("camara-api-demo", secret)

    def _bearer(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    # ── tests ────────────────────────────────────────────────────────────
    def run_all_tests(self) -> bool:
        self.logger.info(f"Starting CAMARA Test Suite against {self.gw}")
        # Northbound is opt-in; if the gateway is unreachable, skip (not fail).
        try:
            requests.get(f"{self.gw}/health", timeout=5)
        except Exception:
            self.logger.warning(f"SKIP: CAMARA gateway unreachable at {self.gw} (northbound not deployed?)")
            return True

        tests = [
            ("Gateway health", self.test_health),
            ("Auth required on /assets", self.test_auth_required),
            ("Capabilities aggregates adapters", self.test_capabilities),
            ("Location retrieval by assetId", self.test_retrieve_by_assetid),
            ("Asset Identity Map seed present", self.test_assets_seed),
            ("Per-org isolation (consumer sees only its org)", self.test_org_isolation),
        ]
        passed = failed = 0
        for name, fn in tests:
            self.logger.test_start(name)
            try:
                ok = fn()
            except Exception as e:
                self.logger.error(f"{name} failed with exception: {e}")
                ok = False
            passed += 1 if ok else 0
            failed += 0 if ok else 1
            self.logger.test_end(name, ok)
        self.logger.info(f"CAMARA Test Results: {passed} passed, {failed} failed")
        return failed == 0

    def test_health(self) -> bool:
        r = requests.get(f"{self.gw}/health", timeout=self.timeout)
        if r.status_code != 200:
            self.logger.error(f"/health HTTP {r.status_code}")
            return False
        self.logger.success("gateway /health 200")
        return True

    def test_auth_required(self) -> bool:
        r = requests.get(f"{self.gw}/assets", timeout=self.timeout)
        if r.status_code != 401:
            self.logger.error(f"/assets without token should be 401, got {r.status_code}")
            return False
        self.logger.success("/assets requires auth (401 without token)")
        return True

    def _need_operator(self) -> str:
        tok = self._operator_token()
        if not tok:
            self.logger.warning("SKIP: CAMARA_CLIENT_SECRET not in env or .testbed.secrets")
        return tok

    def test_capabilities(self) -> bool:
        tok = self._need_operator()
        if not tok:
            return True
        r = requests.get(f"{self.gw}/capabilities", headers=self._bearer(tok), timeout=self.timeout)
        if r.status_code != 200:
            self.logger.error(f"/capabilities HTTP {r.status_code}: {r.text[:200]}")
            return False
        caps = r.json()
        if "mock" not in caps.get("sources", []):
            self.logger.error(f"/capabilities should aggregate the mock adapter; got sources={caps.get('sources')}")
            return False
        self.logger.success(f"/capabilities profile={caps.get('profile')} sources={caps.get('sources')} kinds={caps.get('kinds')}")
        return True

    def test_retrieve_by_assetid(self) -> bool:
        tok = self._need_operator()
        if not tok:
            return True
        r = requests.post(
            f"{self.gw}/location-retrieval/v0.5/retrieve",
            headers={**self._bearer(tok), "Content-Type": "application/json"},
            json={"device": {"assetId": self.demo_asset_id}, "maxAge": 60},
            timeout=self.timeout,
        )
        if r.status_code != 200:
            self.logger.error(f"retrieve HTTP {r.status_code}: {r.text[:200]}")
            return False
        body = r.json()
        center = body.get("area", {}).get("center", {})
        if "latitude" not in center or "longitude" not in center:
            self.logger.error(f"retrieve missing area.center lat/lon: {body}")
            return False
        self.logger.success(
            f"retrieve {self.demo_asset_id}: center=({center.get('latitude')},{center.get('longitude')}) "
            f"source={body.get('source')} kind={body.get('kind')} altitude={body.get('altitude')}")
        return True

    def test_assets_seed(self) -> bool:
        tok = self._need_operator()
        if not tok:
            return True
        r = requests.get(f"{self.gw}/assets", headers=self._bearer(tok), timeout=self.timeout)
        if r.status_code != 200:
            self.logger.error(f"/assets HTTP {r.status_code}: {r.text[:200]}")
            return False
        ids = [a.get("asset_id") for a in (r.json() or {}).get("assets", [])]
        if self.demo_asset_id not in ids:
            self.logger.error(f"/assets missing seed {self.demo_asset_id}; got {ids}")
            return False
        self.logger.success(f"/assets contains seed {self.demo_asset_id} ({len(ids)} asset(s))")
        return True

    def test_org_isolation(self) -> bool:
        """A consumer scoped to org=demo sees its own asset but NOT a foreign-org one.
        Stages a temporary foreign-org asset via the operator (org-less), checks the
        consumer cannot see it while the operator (bypass) can, then restores the
        store in a finally. Requires the operator secret."""
        op = self._need_operator()
        if not op:
            return True
        consumer = self._consumer_token()
        if not consumer:
            self.logger.error("could not obtain camara-api-demo token")
            return False

        # Positive: consumer (org=demo) sees the demo asset.
        r = requests.get(f"{self.gw}/assets", headers=self._bearer(consumer), timeout=self.timeout)
        if r.status_code != 200:
            self.logger.error(f"consumer /assets HTTP {r.status_code}: {r.text[:200]}")
            return False
        if self.demo_asset_id not in [a.get("asset_id") for a in (r.json() or {}).get("assets", [])]:
            self.logger.error(f"consumer (org=demo) should see {self.demo_asset_id}")
            return False

        cur = requests.get(f"{self.gw}/assets", headers=self._bearer(op), timeout=self.timeout)
        original = cur.json() if cur.status_code == 200 else {"version": 2, "assets": []}
        foreign_id = "zzz-isolation-test"
        modified = copy.deepcopy(original)
        modified.setdefault("assets", [])
        modified["assets"] = [a for a in modified["assets"] if a.get("asset_id") != foreign_id]
        modified["assets"].append({
            "asset_id": foreign_id, "positioning_id": foreign_id,
            "kind": "asset", "source": "mock", "org": "zzz-foreign", "label": "isolation test",
        })
        try:
            put = requests.put(
                f"{self.gw}/assets", headers={**self._bearer(op), "Content-Type": "application/json"},
                json=modified, timeout=self.timeout)
            if put.status_code not in (200, 204):
                self.logger.error(f"could not stage foreign asset: HTTP {put.status_code}: {put.text[:200]}")
                return False
            seen = [a.get("asset_id") for a in (requests.get(f"{self.gw}/assets", headers=self._bearer(consumer), timeout=self.timeout).json() or {}).get("assets", [])]
            if foreign_id in seen:
                self.logger.error(f"ISOLATION LEAK: consumer (org=demo) sees foreign-org asset {foreign_id}; got {seen}")
                return False
            op_seen = [a.get("asset_id") for a in (requests.get(f"{self.gw}/assets", headers=self._bearer(op), timeout=self.timeout).json() or {}).get("assets", [])]
            if foreign_id not in op_seen:
                self.logger.error(f"operator (org-less bypass) should see all assets incl {foreign_id}; got {op_seen}")
                return False
            self.logger.success(f"org isolation OK: consumer sees {seen} (no {foreign_id}); operator bypass sees {op_seen}")
            return True
        finally:
            requests.put(
                f"{self.gw}/assets", headers={**self._bearer(op), "Content-Type": "application/json"},
                json=original, timeout=self.timeout)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="CAMARA / Northbound Tests")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()
    suite = CamaraTestSuite(verbose=args.verbose)
    success = suite.run_all_tests()
    if success:
        print("\n🎉 All CAMARA tests passed!")
        sys.exit(0)
    else:
        print("\n💥 Some CAMARA tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()

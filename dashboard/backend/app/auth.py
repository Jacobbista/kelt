"""JWT authentication against the Keycloak realm deployed by phase 08.

Verifies `Authorization: Bearer <jwt>` tokens against the realm JWKS,
extracts realm roles, and exposes FastAPI dependencies for role-based
authorization. The full role-to-endpoint matrix lives in
docs/security/iam.md.

The module degrades gracefully via the `skip_auth` setting: when True
(default until Keycloak is reachable), every request is treated as if
issued by a synthetic `dashboard-admin` principal so the dashboard
remains usable during phased rollout.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import Depends, Header, HTTPException, Query, status
from jose import jwt
from jose.exceptions import JWTError

from app.config import settings

log = logging.getLogger(__name__)


# ── Principal ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Principal:
    """Verified caller extracted from the JWT (or a synthetic admin in skip_auth mode)."""

    subject: str
    username: str
    client_id: str
    roles: frozenset[str] = field(default_factory=frozenset)

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_any_role(self, *roles: str) -> bool:
        return any(r in self.roles for r in roles)


_SYNTHETIC_ADMIN = Principal(
    subject="skip-auth",
    username="skip-auth",
    client_id="skip-auth",
    roles=frozenset({"dashboard-admin", "dashboard-viewer"}),
)


# ── JWKS cache ─────────────────────────────────────────────────────────

class _JwksCache:
    """Threadsafe JWKS cache with on-miss refresh.

    Keycloak rotates signing keys periodically; the cache refreshes when
    a token references a kid not currently in the cache, bounded by
    `_refresh_cooldown` to avoid hammering Keycloak on bogus tokens.
    """

    _refresh_cooldown = 30.0  # seconds

    def __init__(self) -> None:
        self._keys: dict[str, dict[str, Any]] = {}
        self._issuer: str | None = None
        self._last_refresh: float = 0.0
        self._lock = threading.Lock()

    @property
    def issuer(self) -> str | None:
        return self._issuer

    def get_key(self, kid: str) -> dict[str, Any] | None:
        key = self._keys.get(kid)
        if key is not None:
            return key
        # Refresh once per cooldown window to absorb rotation.
        now = time.monotonic()
        with self._lock:
            if now - self._last_refresh < self._refresh_cooldown:
                return self._keys.get(kid)
            self._refresh_locked()
        return self._keys.get(kid)

    def _refresh_locked(self) -> None:
        prefix = settings.keycloak_path_prefix or ""
        realm = settings.keycloak_realm
        base = f"{settings.keycloak_url.rstrip('/')}{prefix}/realms/{realm}"
        try:
            with httpx.Client(timeout=5.0) as client:
                certs = client.get(f"{base}/protocol/openid-connect/certs").raise_for_status().json()
                config = client.get(f"{base}/.well-known/openid-configuration").raise_for_status().json()
        except httpx.HTTPError as exc:
            log.warning("JWKS refresh failed: %s", exc)
            self._last_refresh = time.monotonic()
            return
        self._keys = {k["kid"]: k for k in certs.get("keys", []) if "kid" in k}
        self._issuer = config.get("issuer")
        self._last_refresh = time.monotonic()
        log.info("JWKS refreshed: %d keys, issuer=%s", len(self._keys), self._issuer)


_jwks = _JwksCache()


# ── Token verification ─────────────────────────────────────────────────

def _accepted_clients() -> set[str]:
    return {c.strip() for c in settings.keycloak_accepted_clients.split(",") if c.strip()}


def _verify_token(token: str) -> Principal:
    """Parse and verify a JWT; return Principal or raise 401."""
    try:
        headers = jwt.get_unverified_headers(token)
    except JWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Malformed token: {exc}") from exc

    kid = headers.get("kid")
    if not kid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token missing 'kid' header")

    key = _jwks.get_key(kid)
    if not key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown signing key")

    expected_issuer = _jwks.issuer
    if not expected_issuer:
        # Trigger an initial fetch so issuer becomes available on next call.
        _jwks.get_key(kid)
        expected_issuer = _jwks.issuer
    if not expected_issuer:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Keycloak unreachable")

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[headers.get("alg", "RS256")],
            issuer=expected_issuer,
            options={"verify_aud": False},  # checked manually against accepted clients
        )
    except JWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {exc}") from exc

    azp = claims.get("azp")
    aud = claims.get("aud")
    if isinstance(aud, str):
        aud = [aud]
    accepted = _accepted_clients()
    if azp not in accepted and not (isinstance(aud, list) and any(a in accepted for a in aud)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token not for an accepted client")

    realm_roles = ((claims.get("realm_access") or {}).get("roles") or [])
    return Principal(
        subject=claims.get("sub", ""),
        username=claims.get("preferred_username") or claims.get("email") or claims.get("sub", ""),
        client_id=azp or "",
        roles=frozenset(realm_roles),
    )


# ── FastAPI dependencies ───────────────────────────────────────────────

def get_principal(
    authorization: str | None = Header(default=None),
    access_token: str | None = Query(default=None),
) -> Principal:
    """Resolve the calling principal.

    Tokens may arrive via:
      - HTTP `Authorization: Bearer <jwt>` header (standard for REST)
      - `?access_token=<jwt>` query string (the only way for browser
        WebSocket upgrades, which cannot set custom headers)

    Returns a synthetic admin when settings.skip_auth is True so the
    dashboard remains operable during the rollout window before Keycloak
    is wired up.
    """
    if settings.skip_auth:
        return _SYNTHETIC_ADMIN
    token: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(None, 1)[1].strip()
    elif access_token:
        token = access_token.strip()
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing Bearer token")
    return _verify_token(token)


def require_role(role: str):
    """Dependency factory: require a specific realm role on the principal."""

    def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has_role(role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing role: {role}")
        return principal

    return _dep


def require_any_role(*roles: str):
    """Dependency factory: require at least one of the given realm roles."""

    def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has_any_role(*roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing any of: {','.join(roles)}")
        return principal

    return _dep


# Convenience aliases for the dashboard role model.
require_admin = require_role("dashboard-admin")
require_viewer_or_admin = require_any_role("dashboard-admin", "dashboard-viewer")

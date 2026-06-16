"""Front-door co-branding API.

Reads/writes the welcome-page brand (org name, logo, accent, tagline) stored in
the frontdoor-brand ConfigMap (key brand.json), then rolls the front-door so the
subPath-mounted file is re-read. KELT stays the primary brand; this is the org
co-brand shown under it. Two routers share the /api/v1/branding prefix:
  - read_router  : GET current brand -> _viewer
  - write_router : PUT brand         -> _admin
"""

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.models import BrandRequest
from app.services.audit import write_audit
from app.services.k8s_service import K8sService, get_k8s_service

read_router = APIRouter(prefix="/api/v1/branding", tags=["branding"])
write_router = APIRouter(prefix="/api/v1/branding", tags=["branding"])

_NS = "frontdoor"
_CM = "frontdoor-brand"
_DEPLOY = "frontdoor"
_MAX_LOGO = 512 * 1024  # data-URI cap; the ConfigMap hard limit is ~1 MB
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


@read_router.get("")
def get_brand(k8s: K8sService = Depends(get_k8s_service)) -> dict[str, Any]:
    # Current org co-brand. Degrades to {} (KELT only) if the front-door is not
    # deployed or the ConfigMap is absent.
    try:
        raw = (k8s.get_configmap(_NS, _CM).get("data") or {}).get("brand.json", "")
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


@write_router.put("")
def set_brand(req: BrandRequest, k8s: K8sService = Depends(get_k8s_service)) -> dict[str, Any]:
    if req.accent and not _HEX.match(req.accent.strip()):
        raise HTTPException(status_code=400, detail="accent must be a #RRGGBB hex color")
    if req.org_logo and len(req.org_logo) > _MAX_LOGO:
        raise HTTPException(status_code=400, detail="logo too large (max 512 KB); use a smaller image")
    if req.logo_bg and req.logo_bg not in ("light", "dark", "glass"):
        raise HTTPException(status_code=400, detail="logo_bg must be 'light', 'dark', 'glass', or empty")
    # Keep only set fields; an empty field clears it (KELT-only / default tagline).
    brand = {
        k: v for k, v in {
            "org_name": req.org_name.strip(),
            "org_logo": req.org_logo.strip(),
            "accent": req.accent.strip(),
            "tagline": req.tagline.strip(),
            "logo_bg": req.logo_bg.strip(),
        }.items() if v
    }
    try:
        k8s.apply_configmap(_NS, _CM, {"brand.json": json.dumps(brand)})
        k8s.restart_deployment(_NS, _DEPLOY)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"could not apply brand: {exc}")
    write_audit("branding.set", {"org_name": brand.get("org_name", ""), "has_logo": bool(brand.get("org_logo"))})
    # Never echo the (large) logo data-URI back.
    return {"status": "applied", "org_name": brand.get("org_name", ""), "has_logo": bool(brand.get("org_logo"))}

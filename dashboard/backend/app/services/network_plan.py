"""The testbed network plan, read from ansible group_vars/all.yml.

all.yml is the single source of every address the dashboard reasons about
(overlay planes, fixed NF endpoints, UE pools, physical RAN, node IPs) and of the
namespace names. Addresses are read per call rather than cached: an operator can
edit them and re-run a phase while the backend keeps running. Namespaces are read
once at import (module constants); renaming one needs a redeploy anyway. A
missing key is a deployment bug and raises.
"""
import re
from pathlib import Path
from typing import Any

import yaml

GROUP_VARS = Path("/home/vagrant/ansible-ro/group_vars/all.yml")

# Every overlay plane has a pool NAD (<plane>-net) and a static NAD (<plane>-static)
# on the same bridge br-<plane>; see phase 04 multus_install.
_OVERLAY_NAD = re.compile(r"^(n[1-6][a-z]?)-(net|static)$")


def load_plan() -> dict[str, Any]:
    return yaml.safe_load(GROUP_VARS.read_text()) or {}


def nad_plane(nad_name: str) -> str | None:
    """Plane of an overlay NAD ("n3-static" -> "n3"), None for any other NAD."""
    m = _OVERLAY_NAD.match(nad_name)
    return m.group(1) if m else None


def plan_value(key: str) -> Any:
    data = load_plan()
    if key not in data:
        raise KeyError(f"{key} is missing from {GROUP_VARS}")
    return data[key]

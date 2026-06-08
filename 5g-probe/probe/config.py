"""
Central defaults for the probe host app.

benchmark_targets() feeds UI/API presets only: shortcuts in the target IP datalist.
Every benchmark still accepts any reachable IP typed manually—presets do not exclude
each other; you can run one test toward UPF, another toward MEC, another toward any lab IP.

- UPF-Cloud (or equivalent): default anchor for PDU reachability (see FIVEG_PROBE_UPF_TARGET).
- MEC iperf (optional): post-UPF decapsulated path when FIVEG_PROBE_MEC_IPERF_TARGET is set.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

PACKAGE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _env_tunnel_debug() -> bool:
    """Verbose Web UI tunnel logs; set by run-probe.sh via PROBE_WEBUI_TUNNEL_DEBUG."""
    v = os.environ.get("PROBE_WEBUI_TUNNEL_DEBUG", "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    return False


DEBUG_WEBUI_TUNNEL = _env_tunnel_debug()

DEFAULT_UPF_CLOUD_IP = os.environ.get("FIVEG_PROBE_UPF_TARGET", "10.45.0.1")

DEFAULT_ROUTE_PROBE = os.environ.get("FIVEG_PROBE_ROUTE_PROBE", DEFAULT_UPF_CLOUD_IP)

MEC_IPERF_TARGET = os.environ.get("FIVEG_PROBE_MEC_IPERF_TARGET", "").strip()


def benchmark_targets() -> List[Dict[str, Any]]:
    """Named presets for datalist /api/config; users may always enter another IP."""
    targets: List[Dict[str, Any]] = [
        {
            "id": "upf_cloud",
            "label": "UPF-Cloud (via PDU session)",
            "ip": DEFAULT_UPF_CLOUD_IP,
            "path": "encapsulated_user_plane_through_upf",
        },
    ]
    if MEC_IPERF_TARGET:
        targets.append(
            {
                "id": "mec_post_upf",
                "label": "MEC iperf (post-UPF decapsulated)",
                "ip": MEC_IPERF_TARGET,
                "path": "decapsulated_after_upf",
            }
        )
    return targets

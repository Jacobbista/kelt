"""Cluster time synchronization check and correction.

Fetches UTC time from all testbed VMs via SSH and compares against the
local (ansible) clock to detect drift. Can also force chrony to step
the clock on all nodes when drift exceeds tolerance.
"""

import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

from app.config import settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/time", tags=["time-sync"])

_SSH_HOSTS = ["master", "worker", "edge"]
_SSH_BASE = [
    "ssh",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "LogLevel=ERROR",
    "-o", "BatchMode=yes",
]


def _ssh_run(host: str, cmd: str, timeout: int = 5) -> subprocess.CompletedProcess:
    """Run a command on a remote VM via SSH."""
    return subprocess.run(
        [*_SSH_BASE, "-o", f"ConnectTimeout={timeout}", host, cmd],
        capture_output=True,
        text=True,
        timeout=timeout + 2,
        check=False,
    )


def _fmt_utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _get_remote_time_with_rtt(host: str, timeout: int = 5) -> dict[str, Any]:
    """Get UTC time from a remote VM, compensating for SSH round-trip.

    Records local time before and after the SSH call. The remote command
    executed roughly at the midpoint, so we use (t1+t2)/2 as the local
    reference for this specific host. This eliminates the false drift
    caused by sequential SSH latency.
    """
    try:
        t1 = datetime.now(timezone.utc)
        proc = _ssh_run(host, "date -u +%Y-%m-%dT%H:%M:%S.%3NZ", timeout)
        t2 = datetime.now(timezone.utc)

        if proc.returncode != 0 or not proc.stdout.strip():
            return {"host": host, "time_utc": None, "offset_ms": None, "reachable": False}

        remote_ts = proc.stdout.strip()
        remote_dt = _parse_iso(remote_ts)
        if remote_dt is None:
            return {"host": host, "time_utc": remote_ts, "offset_ms": None, "reachable": True}

        # Use midpoint of local before/after as the reference for this host
        midpoint = t1 + (t2 - t1) / 2
        rtt_ms = int((t2 - t1).total_seconds() * 1000)
        offset_ms = int((remote_dt - midpoint).total_seconds() * 1000)

        return {
            "host": host,
            "time_utc": remote_ts,
            "offset_ms": offset_ms,
            "rtt_ms": rtt_ms,
            "reachable": True,
        }
    except Exception as exc:
        log.debug("Failed to get time from %s: %s", host, exc)
        return {"host": host, "time_utc": None, "offset_ms": None, "reachable": False}


def _force_sync_host(host: str, timeout: int = 5) -> dict[str, Any]:
    """Force chrony to step the clock on a remote VM."""
    try:
        proc = _ssh_run(host, "sudo chronyc -a makestep", timeout)
        return {
            "host": host,
            "success": proc.returncode == 0,
            "output": proc.stdout.strip() if proc.stdout else proc.stderr.strip(),
        }
    except Exception as exc:
        log.warning("Failed to force sync on %s: %s", host, exc)
        return {"host": host, "success": False, "output": str(exc)}


def _parse_iso(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


@router.get("/sync")
def time_sync() -> dict[str, Any]:
    """Check time synchronization across all testbed VMs."""
    ref_now = datetime.now(timezone.utc)

    nodes: dict[str, dict[str, Any]] = {
        "ansible": {
            "host": "ansible",
            "time_utc": _fmt_utc(ref_now),
            "offset_ms": 0,
            "reachable": True,
        }
    }

    # Run SSH calls in parallel to minimize total wait time
    max_drift = 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_get_remote_time_with_rtt, host): host for host in _SSH_HOSTS}
        for future in as_completed(futures):
            result = future.result()
            host = result["host"]
            nodes[host] = result
            if result.get("offset_ms") is not None:
                abs_offset = abs(result["offset_ms"])
                if abs_offset > max_drift:
                    max_drift = abs_offset

    return {
        **nodes,
        "reference_utc": _fmt_utc(ref_now),
        "max_drift_ms": max_drift,
        "in_sync": max_drift < 1000,
    }


@router.post("/force-sync")
def force_sync() -> dict[str, Any]:
    """Force chrony makestep on all VMs, then re-check time sync."""
    # Step 1: force sync on local (ansible) node
    local_result = {"host": "ansible", "success": False, "output": ""}
    try:
        proc = subprocess.run(
            ["sudo", "chronyc", "-a", "makestep"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        local_result["success"] = proc.returncode == 0
        local_result["output"] = proc.stdout.strip() if proc.stdout else proc.stderr.strip()
    except Exception as exc:
        local_result["output"] = str(exc)

    # Step 2: force sync on remote hosts in parallel
    results = [local_result]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_force_sync_host, host) for host in _SSH_HOSTS]
        for future in as_completed(futures):
            results.append(future.result())

    # Step 3: re-read time sync status after correction
    updated = time_sync()

    return {
        "sync_results": results,
        **updated,
    }

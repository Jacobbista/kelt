"""Disk usage breakdown for the cluster nodes, plus the reclaim actions.

Why a breakdown rather than one percentage: the bulk of a KELT node's disk is
extracted container images (containerd overlayfs snapshots), not the things an
operator instinctively blames. Pruning the in-cluster registry, for instance,
reclaims very little because the registry only stores compressed blobs while the
GB live in the snapshots on the node. Showing those as separate numbers is what
stops someone from running the wrong cleanup and wondering why the disk did not
move.

Sizes come from `du`, which walks the tree and is therefore slow (seconds on a
multi-GB snapshot dir). Results are cached and the caller asks for a refresh
explicitly, so opening a page never blocks on a filesystem walk.

Node access follows the same pattern as OVSService: ssh with a strict command
allow-list, never a shell string built from request input.
See docs/operations/handbook.md.
"""

import json
import logging
import re
import subprocess
import time
from typing import Any

from fastapi import HTTPException, status

from app.config import settings

log = logging.getLogger(__name__)

# k3s layout. Snapshots are extracted image layers (the big one); content is the
# compressed blob store; storage holds the local-path PVCs.
CONTAINERD_ROOT = "/var/lib/rancher/k3s/agent/containerd"
SNAPSHOTS_DIR = f"{CONTAINERD_ROOT}/io.containerd.snapshotter.v1.overlayfs"
CONTENT_DIR = f"{CONTAINERD_ROOT}/io.containerd.content.v1.content"
PVC_ROOT = "/var/lib/rancher/k3s/storage"
# Matches the journald cap phase 01 configures, so the estimate and the action agree.
JOURNAL_CAP_BYTES = 500 * 1024**2

# local-path names its directories <pvc-uid>_<namespace>_<claim>, which is the
# only place the claim identity survives on disk.
_PVC_DIR = re.compile(r"^(pvc-[0-9a-f-]+)_([^_]+)_(.+)$")

# Digests parsed out of the registry's own dry-run output. Validated before being
# interpolated into a shell command, so a surprise in that output can never turn
# into an injected argument.
_BLOB_LINE = re.compile(r"^blob eligible for deletion: (sha256:[0-9a-f]{64})\s*$", re.M)
REGISTRY_BLOB_ROOT = "/var/lib/registry/docker/registry/v2/blobs/sha256"

_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 300.0


def _fmt_error(proc: subprocess.CompletedProcess) -> str:
    err = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return err[: settings.shell_max_output_bytes]


class StorageService:
    """Read disk usage from a node and run the reclaim actions."""

    # Prefix allow-list: a command must start with one of these exactly.
    ALLOWED_PREFIXES: list[list[str]] = [
        ["df"],
        ["sudo", "du"],
        ["sudo", "sh", "-c"],          # only ever with the fixed scripts below
        ["journalctl", "--disk-usage"],
        ["sudo", "journalctl", "--vacuum-size=500M"],
        ["sudo", "k3s", "crictl", "rmi", "--prune"],
        ["sudo", "k3s", "crictl", "images"],
        ["sudo", "k3s", "crictl", "ps"],
    ]

    def __init__(self, host: str | None = None, k8s: Any = None) -> None:
        self.host = host or settings.worker_ssh_host
        # Only the registry garbage-collect needs cluster access; everything else
        # is node-level, so k8s stays optional.
        self.k8s = k8s

    # ── node access ───────────────────────────────────────────────────────────

    def _is_allowed(self, command: list[str]) -> bool:
        return any(command[: len(p)] == p for p in self.ALLOWED_PREFIXES)

    def _run_remote(self, command: list[str], timeout: int | None = None) -> str:
        if not self._is_allowed(command):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Command is not allowed by policy",
            )
        wrapped = [
            "ssh",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "LogLevel=ERROR",
            "-o", "BatchMode=yes",
            self.host,
            " ".join(command),
        ]
        try:
            proc = subprocess.run(
                wrapped, capture_output=True, text=True, check=False,
                timeout=timeout or settings.shell_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail="Command timeout") from exc
        if proc.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"Storage command failed ({proc.returncode}): {_fmt_error(proc)}",
            )
        return proc.stdout or ""

    # ── read ──────────────────────────────────────────────────────────────────

    def _filesystem(self) -> dict[str, int]:
        """Root filesystem totals in bytes. Cheap, so never cached."""
        out = self._run_remote(["df", "-B1", "--output=size,used,avail", "/"])
        rows = [r for r in out.splitlines() if r.strip()]
        try:
            size, used, avail = (int(v) for v in rows[-1].split())
        except (IndexError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="Could not parse df output") from exc
        return {"total": size, "used": used, "free": avail,
                "used_pct": round(used * 100 / size, 1) if size else 0.0}

    def _du_bytes(self, paths: list[str], timeout: int = 120) -> dict[str, int]:
        """`du -sb` for each path. A missing path is reported as 0 rather than an
        error: an optional component (the registry, say) may simply not be deployed."""
        out = self._run_remote(["sudo", "du", "-sb", *paths], timeout=timeout)
        sizes: dict[str, int] = {p: 0 for p in paths}
        for line in out.splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) == 2 and parts[0].isdigit():
                sizes[parts[1].strip()] = int(parts[0])
        return sizes

    def _pvcs(self, timeout: int = 120) -> list[dict[str, Any]]:
        """Per-claim sizes, with namespace and claim name recovered from the
        local-path directory naming convention."""
        try:
            out = self._run_remote(
                ["sudo", "sh", "-c", f"'du -sb {PVC_ROOT}/*/ 2>/dev/null'"], timeout=timeout
            )
        except HTTPException:
            return []
        items: list[dict[str, Any]] = []
        for line in out.splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) != 2 or not parts[0].isdigit():
                continue
            dirname = parts[1].strip().rstrip("/").rsplit("/", 1)[-1]
            m = _PVC_DIR.match(dirname)
            items.append({
                "namespace": m.group(2) if m else None,
                "claim": m.group(3) if m else dirname,
                "bytes": int(parts[0]),
            })
        return sorted(items, key=lambda i: i["bytes"], reverse=True)

    def _journal_bytes(self) -> int:
        out = self._run_remote(["journalctl", "--disk-usage"])
        m = re.search(r"([\d.]+)([KMGT])", out)
        if not m:
            return 0
        mult = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}[m.group(2)]
        return int(float(m.group(1)) * mult)

    def usage(self, refresh: bool = False) -> dict[str, Any]:
        """Filesystem totals plus the breakdown that explains them."""
        key = f"usage:{self.host}"
        hit = _CACHE.get(key)
        if hit and not refresh and time.time() - hit[0] < _CACHE_TTL:
            cached = dict(hit[1])
            cached["filesystem"] = self._filesystem()  # always live, it is cheap
            cached["cached"] = True
            return cached

        du = self._du_bytes([SNAPSHOTS_DIR, CONTENT_DIR])
        pvcs = self._pvcs()
        # The registry is a PVC like any other; it is called out separately because
        # it is the one an operator is most likely to blame for a full disk.
        registry = next((p for p in pvcs if (p["claim"] or "").startswith("registry")), None)
        result = {
            "node": self.host,
            "filesystem": self._filesystem(),
            "containerd": {
                "snapshots": du.get(SNAPSHOTS_DIR, 0),
                "content": du.get(CONTENT_DIR, 0),
            },
            "pvcs": pvcs,
            "pvcs_total": sum(p["bytes"] for p in pvcs),
            "registry": registry["bytes"] if registry else 0,
            "journal": self._journal_bytes(),
            "cached": False,
            "measured_at": time.time(),
        }
        _CACHE[key] = (time.time(), result)
        return result

    # ── preview ───────────────────────────────────────────────────────────────

    def preview(self) -> dict[str, Any]:
        """What each reclaim action would free, so nothing is a blind click.

        These are ESTIMATES and the UI says so. The image figure sums the size
        crictl reports per unused image, and layers shared between images are
        counted once per image, so the real saving is usually a little lower.
        The actual freed amount is measured from the filesystem after the action.
        """
        return {
            "prune_images": self._unused_image_estimate(),
            "vacuum_journal": self._journal_trim_estimate(),
        }

    def _unused_image_estimate(self) -> dict[str, Any]:
        try:
            images = json.loads(
                self._run_remote(["sudo", "k3s", "crictl", "images", "-o", "json"], timeout=60)
            ).get("images") or []
            containers = json.loads(
                self._run_remote(["sudo", "k3s", "crictl", "ps", "-a", "-o", "json"], timeout=60)
            ).get("containers") or []
        except (HTTPException, json.JSONDecodeError, TypeError):
            return {"available": False}

        # A container pins its image by any of the identifiers crictl reports, so
        # collect them all rather than guessing which form is in use.
        used: set[str] = set()
        for c in containers:
            if c.get("imageRef"):
                used.add(c["imageRef"])
            spec = c.get("image") or {}
            for key in ("image", "userSpecifiedImage"):
                if spec.get(key):
                    used.add(spec[key])

        unused_bytes = 0
        unused_count = 0
        for img in images:
            ids = {img.get("id", "")} | set(img.get("repoTags") or []) | set(img.get("repoDigests") or [])
            if not (ids & used):
                unused_bytes += int(img.get("size") or 0)
                unused_count += 1
        return {"available": True, "count": unused_count, "total": len(images),
                "bytes": unused_bytes}

    def _journal_trim_estimate(self) -> dict[str, Any]:
        current = self._journal_bytes()
        return {"available": True, "bytes": max(0, current - JOURNAL_CAP_BYTES),
                "current": current, "cap": JOURNAL_CAP_BYTES}

    # ── reclaim ───────────────────────────────────────────────────────────────

    def prune_images(self) -> dict[str, Any]:
        """Drop image layers no container references. This is the action that
        actually moves the needle, since snapshots are the largest consumer."""
        before = self._filesystem()
        self._run_remote(["sudo", "k3s", "crictl", "rmi", "--prune"], timeout=300)
        _CACHE.pop(f"usage:{self.host}", None)
        after = self._filesystem()
        return {"action": "prune-images", "freed": max(0, before["used"] - after["used"]),
                "free_after": after["free"]}

    def garbage_collect_registry(self, dry_run: bool = True) -> dict[str, Any]:
        """Delete blobs the in-cluster registry no longer references. Expect a small
        number: the registry stores compressed blobs, so this is hygiene, not space
        recovery. Defaults to a dry run because the real one rewrites the store.
        Requires REGISTRY_STORAGE_DELETE_ENABLED, which phase 12 sets."""
        if self.k8s is None:
            raise HTTPException(status_code=503, detail="Cluster access is not available")
        pods = self.k8s.core.list_namespaced_pod(
            namespace="apps", label_selector="app=registry")
        running = [p for p in pods.items if p.status.phase == "Running"]
        if not running:
            raise HTTPException(status_code=404, detail="Registry pod is not running")
        cmd = ["registry", "garbage-collect", "--delete-untagged",
               "/etc/docker/registry/config.yml"]
        if dry_run:
            cmd.insert(2, "--dry-run")
        pod = running[0].metadata.name
        out = self.k8s.exec_in_pod("apps", pod, cmd) or ""
        digests = _BLOB_LINE.findall(out)
        return {
            "action": "registry-gc",
            "dry_run": dry_run,
            "blobs_eligible": len(digests),
            # A blob count says nothing about whether the run is worth doing, so
            # report the bytes those blobs occupy. Only meaningful on a dry run:
            # after a real one the files are already gone.
            "bytes": self._blob_bytes(pod, digests) if dry_run else None,
            "output": out[-4000:],
        }

    def _blob_bytes(self, pod: str, digests: list[str]) -> int | None:
        """Total size of the given blobs, summed inside the registry pod. Returns
        None when it cannot be determined, so the UI can stay silent instead of
        claiming zero."""
        if not digests:
            return 0
        paths = " ".join(
            f"{REGISTRY_BLOB_ROOT}/{d[7:9]}/{d[7:]}/data" for d in digests
        )
        try:
            out = self.k8s.exec_in_pod(
                "apps", pod,
                ["sh", "-c", f"stat -c %s {paths} 2>/dev/null | awk '{{t+=$1}} END {{print t+0}}'"],
            )
        except Exception as exc:
            log.info("blob size lookup failed: %s", exc)
            return None
        try:
            return int((out or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            return None

    def vacuum_journal(self) -> dict[str, Any]:
        """Trim systemd journals to the same 500M cap phase 01 configures, so a
        node that predates that setting can be brought in line without a re-run."""
        before = self._filesystem()
        self._run_remote(["sudo", "journalctl", "--vacuum-size=500M"], timeout=120)
        _CACHE.pop(f"usage:{self.host}", None)
        after = self._filesystem()
        return {"action": "vacuum-journal", "freed": max(0, before["used"] - after["used"]),
                "free_after": after["free"]}

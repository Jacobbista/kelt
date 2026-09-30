"""Pieces and operations for the dashboard: a thin client of the runner.

The runner (ansible/tools/kelt-piece, on this VM) is the only thing that runs a
piece and writes the records; this module lists the registry, starts pieces,
reads records, and adds the piece's read-back check once a run ends with exit
0 ("done" means the state was read back, not that the command exited 0).
"""
import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

import yaml

ANSIBLE_DIR = Path("/home/vagrant/ansible-ro")
RUNNER = ANSIBLE_DIR / "tools" / "kelt-piece"
OPS_DIR = Path("/home/vagrant/.kelt/operations")
PUBLIC = ("title", "tier", "changes", "stops", "takes_s", "confirm_word")
# The read-back only means something right after the run: a run opened later
# (a CLI run seen days after) is recorded as "not read back", never judged by
# the state of the testbed today.
CHECK_WINDOW_S = 600
ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9]{6}-[a-z][a-z0-9_]{0,40}$")
# The runner checks the same bounds; checking here keeps a bad value from
# reaching it at all.
# How many runs the list returns, newest first (the page says so).
LIST_LIMIT = 50
RETENTION_BOUNDS = {"max_age_days": (1, 3650), "max_mb": (1, 10240)}


def _registry() -> dict[str, Any]:
    return yaml.safe_load((ANSIBLE_DIR / "pieces.yml").read_text()) or {}


def _runner(args: list[str], timeout: int = 20) -> str:
    out = subprocess.run(["/usr/bin/python3", str(RUNNER), *args], capture_output=True, text=True,
                         timeout=timeout, check=True)
    return out.stdout


def _parse_ts(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def _store_check(rec: dict[str, Any]) -> None:
    """Add the check to the record the runner wrote, atomically (the runner's
    list/show may read it at the same time)."""
    path = OPS_DIR / f"{rec['id']}.json"
    data = json.loads(path.read_text())
    data["check"] = rec["check"]
    tmp = OPS_DIR / f".{rec['id']}.check.tmp"
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


def list_pieces() -> dict[str, dict[str, Any]]:
    return {n: {k: p.get(k) for k in PUBLIC} for n, p in _registry().items()}


def start(name: str, user: str, confirm: str | None = None) -> dict[str, Any]:
    """Start a piece. `confirm` is the word the admin typed; the runner refuses
    a piece with a confirm_word without it (so the API cannot skip it either)."""
    if name not in _registry():
        raise KeyError(name)
    # --user=<name>: a name that starts with "-" stays a value, not an option.
    args = ["start", name, "--source", "dashboard", f"--user={user or 'unknown'}"]
    if confirm:
        args.append(f"--confirm={confirm}")
    return json.loads(_runner(args))


def list_operations(state: str | None = None) -> list[dict[str, Any]]:
    """The newest LIST_LIMIT runs. `reads_back` says whether the run's piece has
    a read-back: a run that exited 0 is done only once that ran (it runs when
    the run is opened, see get_operation)."""
    args = ["list", "--json", "--limit", str(LIST_LIMIT)] + (["--state", state] if state else [])
    reg = _registry()
    recs = json.loads(_runner(args))
    for rec in recs:
        rec["reads_back"] = bool(reg.get(rec.get("piece"), {}).get("check"))
        if (rec.get("check") or {}).get("ok") is False:
            rec["state"] = "failed"
    return recs


def get_operation(op_id: str, checks: dict[str, Callable[[], tuple[bool, str]]],
                  now: dt.datetime | None = None) -> dict[str, Any]:
    if not ID_RE.match(op_id or ""):
        raise KeyError(op_id)
    rec = json.loads(_runner(["show", op_id]))
    piece = _registry().get(rec.get("piece"), {})
    if rec.get("exit") == 0 and "check" not in rec and piece.get("check") in checks:
        now = now or dt.datetime.now(dt.timezone.utc)
        ended = _parse_ts(rec["ended"]) if rec.get("ended") else now
        if (now - ended).total_seconds() <= CHECK_WINDOW_S:
            ok, message = checks[piece["check"]]()
            # None: the state is still settling (an AMF being replaced). Not
            # stored, so the next read judges again, until the window closes.
            rec["check"] = {"ok": None, "pending": True, "message": message} if ok is None else {"ok": bool(ok), "message": message}
        else:
            rec["check"] = {"ok": None, "message": "not read back: opened after the run ended"}
        if not rec["check"].get("pending"):
            _store_check(rec)
    if rec.get("check") and rec["check"]["ok"] is False:
        rec["state"] = "failed"
    return rec


def _usage() -> dict[str, Any]:
    return json.loads(_runner(["usage", "--json"]))


def retention() -> dict[str, Any]:
    """The record's limits and what it holds now."""
    return {**json.loads(_runner(["retention", "--json"])), **_usage()}


def set_retention(max_age_days: int, max_mb: int) -> dict[str, Any]:
    for key, value in (("max_age_days", max_age_days), ("max_mb", max_mb)):
        lo, hi = RETENTION_BOUNDS[key]
        if type(value) is not int or not lo <= value <= hi:
            raise ValueError(f"{key} must be a whole number between {lo} and {hi}")
    limits = json.loads(_runner(["retention", "--max-age-days", str(max_age_days),
                                 "--max-mb", str(max_mb), "--json"]))
    return {**limits, **_usage()}

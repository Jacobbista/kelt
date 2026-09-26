"""Measured windows and discarded runs of one run directory (window.json).

The resource-use campaign reads the windows of the other campaigns' runs, so
every driver opens a window when the measured part starts and closes it when it
ends. Owner: experiments/README.md.
"""
from __future__ import annotations

import datetime
import json
import os
import sys


def _path(run_dir: str) -> str:
    return os.path.join(run_dir, "window.json")


def _load(run_dir: str) -> dict:
    try:
        with open(_path(run_dir)) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {"windows": [], "discarded": []}


def _save(run_dir: str, doc: dict) -> None:
    with open(_path(run_dir), "w") as fh:
        json.dump(doc, fh, indent=2)


def _now() -> tuple[str, str]:
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ"), now.astimezone().isoformat()


def open_window(run_dir: str, label: str) -> None:
    doc = _load(run_dir)
    utc, local = _now()
    doc["windows"].append({"label": label, "start_utc": utc, "start_local": local, "end_utc": None})
    _save(run_dir, doc)


def close_window(run_dir: str, label: str) -> None:
    doc = _load(run_dir)
    for w in reversed(doc["windows"]):
        if w["label"] == label and w["end_utc"] is None:
            w["end_utc"], w["end_local"] = _now()
            _save(run_dir, doc)
            return
    raise KeyError(f"no open window {label!r} in {run_dir}")


def windows(run_dir: str) -> list[dict]:
    return _load(run_dir)["windows"]


def add_discarded(run_dir: str, what: str, reason: str) -> None:
    doc = _load(run_dir)
    doc.setdefault("discarded", []).append({"what": what, "reason": reason})
    _save(run_dir, doc)


def discarded(run_dir: str) -> list[dict]:
    return _load(run_dir).get("discarded", [])


if __name__ == "__main__":
    cmd, run_dir, *rest = sys.argv[1:]
    {"open": lambda: open_window(run_dir, rest[0]),
     "close": lambda: close_window(run_dir, rest[0]),
     "discard": lambda: add_discarded(run_dir, rest[0], rest[1])}[cmd]()

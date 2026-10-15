#!/usr/bin/env python3
"""The synthetic assets of the response-time load condition (`local50`).

  load_assets.py ids <prefix> <n>                   the ids, comma-separated
  load_assets.py add <prefix> <n> <org> < store     the store with the assets added
  load_assets.py remove <prefix> < store            the store without them
  load_assets.py points <n> <width_m> <depth_m>     one "x y" per asset, spread over the room
  load_assets.py bodies <prefix> <n>                one retrieve body per asset, maxAge=0

A store is the gateway's asset store as GET /assets returns it to the operator
and PUT /assets takes it back. Each load asset has one synthetic capability
whose positioningId is the asset id. Owner: experiments/exposure/README.md.
"""
from __future__ import annotations

import json
import math
import sys


def ids(prefix: str, n: int) -> list[str]:
    return [f"{prefix}{i:02d}" for i in range(1, n + 1)]


def remove(store: dict, prefix: str) -> dict:
    out = dict(store)
    out["assets"] = [a for a in store.get("assets", []) if not str(a.get("assetId", "")).startswith(prefix)]
    return out


def add(store: dict, prefix: str, n: int, org: str) -> dict:
    out = remove(store, prefix)
    out["assets"] = out["assets"] + [
        {"assetId": i, "kind": "asset", "org": org, "label": "response-time load",
         "capabilities": [{"source": "synthetic", "positioningId": i}]}
        for i in ids(prefix, n)]
    return out


def points(n: int, width: float, depth: float) -> list[tuple[float, float]]:
    """A grid of n points inside the room, one cell per asset, each at its
    cell's centre."""
    cols = math.ceil(math.sqrt(n * width / depth)) if depth > 0 else n
    rows = math.ceil(n / cols)
    return [(round((k % cols + 0.5) * width / cols, 2), round((k // cols + 0.5) * depth / rows, 2))
            for k in range(n)]


def bodies(prefix: str, n: int) -> list[str]:
    return [json.dumps({"device": {"assetId": i}, "maxAge": 0}) for i in ids(prefix, n)]


def main() -> int:
    cmd, *rest = sys.argv[1:] or [""]
    if cmd == "ids":
        print(",".join(ids(rest[0], int(rest[1]))))
    elif cmd == "add":
        print(json.dumps(add(json.load(sys.stdin), rest[0], int(rest[1]), rest[2])))
    elif cmd == "remove":
        print(json.dumps(remove(json.load(sys.stdin), rest[0])))
    elif cmd == "points":
        for x, y in points(int(rest[0]), float(rest[1]), float(rest[2])):
            print(x, y)
    elif cmd == "bodies":
        print("\n".join(bodies(rest[0], int(rest[1]))))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

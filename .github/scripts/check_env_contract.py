#!/usr/bin/env python3
"""Anti-drift cross-check: every required env var declared by an upstream
5g-northbound service contract must be provided by the matching testbed
ConfigMap template (or by a known inline env in the Deployment).

Usage:
    check_env_contract.py <path-to-5g-northbound-checkout>

The script does NOT vendor the upstream repo; CI clones it at a pinned ref and
passes the path here. Exit non-zero (with a report) when a required key is
missing, so ConfigMap/contract drift fails the build.
"""

import re
import sys
from pathlib import Path

import yaml

# Service -> where we provide its env in this repo. `configmap` is the Jinja2
# ConfigMap template whose `data:` keys we parse; `inline` lists keys provided
# directly as Deployment env (not in the ConfigMap).
REPO_ROOT = Path(__file__).resolve().parents[2]
PHASES = REPO_ROOT / "ansible" / "phases"
MAP = {
    "camara-gateway": {
        "configmap": PHASES / "10-northbound/roles/camara_gateway/templates/camara-config.yaml.j2",
        "inline": {"CAMARA_CLIENT_SECRET"},
    },
    "positioning-engine": {
        "configmap": PHASES / "10-northbound/roles/positioning_engine/templates/positioning-config.yaml.j2",
        "inline": set(),
    },
}

# Matches `  SOME_KEY:` data entries (two-space indent) in a ConfigMap template.
DATA_KEY = re.compile(r"^\s{2}([A-Z][A-Z0-9_]*)\s*:")


def configmap_keys(template: Path) -> set[str]:
    keys: set[str] = set()
    in_data = False
    for line in template.read_text().splitlines():
        if line.startswith("data:"):
            in_data = True
            continue
        if in_data:
            # A new top-level key (no indent) ends the data block.
            if line and not line.startswith(" "):
                break
            m = DATA_KEY.match(line)
            if m:
                keys.add(m.group(1))
    return keys


def required_vars(contract: Path) -> list[str]:
    data = yaml.safe_load(contract.read_text()) or {}
    return [v["name"] for v in data.get("required", []) if isinstance(v, dict) and "name" in v]


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_env_contract.py <5g-northbound-checkout>", file=sys.stderr)
        return 2
    upstream = Path(sys.argv[1])
    failures = []
    for svc, where in MAP.items():
        contract = upstream / "services" / svc / "env.contract.yaml"
        if not contract.exists():
            failures.append(f"{svc}: contract not found at {contract} (upstream layout changed?)")
            continue
        template = where["configmap"]
        if not template.exists():
            failures.append(f"{svc}: testbed ConfigMap template not found at {template}")
            continue
        provided = configmap_keys(template) | where["inline"]
        missing = [r for r in required_vars(contract) if r not in provided]
        if missing:
            failures.append(f"{svc}: ConfigMap missing required keys {missing} (provided: {sorted(provided)})")
        else:
            print(f"OK  {svc}: all required env vars provided")
    if failures:
        print("\nENV CONTRACT DRIFT:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("\nNo env-contract drift detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

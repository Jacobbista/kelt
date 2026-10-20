#!/usr/bin/env python3
"""The recorded examples of the response-time campaign: one exchange each, kept
for an appendix, not measured.

  examples.py --gateway URL --token-file F --other-token-file G --asset ID --out DIR

- verify_inside: verification for the asset, a circle of 50 m centred on its
  last position;
- verify_partial: a circle of 2 m centred 1.5 m east of it;
- public_phone: a retrieval naming the device by a phone number;
- other_org: a retrieval for the asset with the token of another organisation.

The last position comes from a retrieval made first (retrieve.json). Each
example is written as <case>.json: the request (path, body, which token, never
the token itself) and the response (status, x-correlator, body). The stream
message example is the stream condition's first_message.json.
Owner: experiments/exposure/README.md.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verification import run_case  # noqa: E402

RETRIEVE = "/location-retrieval/v0.5/retrieve"
VERIFY = "/location-verification/v3/verify"
PHONE = "+46700000000"


def east(lat: float, lon: float, metres: float) -> float:
    return lon + metres / (111320.0 * math.cos(math.radians(lat)))


def cases(asset: str, lat: float, lon: float) -> list[tuple[str, str, dict, str]]:
    def circle(clat, clon, r):
        return {"areaType": "CIRCLE", "center": {"latitude": clat, "longitude": clon}, "radius": r}
    return [
        ("verify_inside", VERIFY, {"device": {"assetId": asset}, "area": circle(lat, lon, 50)}, "own"),
        ("verify_partial", VERIFY, {"device": {"assetId": asset}, "area": circle(lat, east(lat, lon, 1.5), 2)}, "own"),
        ("public_phone", RETRIEVE, {"device": {"phoneNumber": PHONE}}, "own"),
        ("other_org", RETRIEVE, {"device": {"assetId": asset}}, "other organisation"),
    ]


def record(gw: str, out: str, case: str, path: str, body: dict, token: str, which: str) -> dict:
    r = run_case(case, "POST", gw + path, {"Authorization": f"Bearer {token}"}, body)
    with open(os.path.join(out, f"{case}.json"), "w") as fh:
        json.dump({"request": {"method": "POST", "path": path, "token": which, "body": body},
                   "response": {"status": r["status"], "x_correlator": r["x_correlator"], "body": r["body"],
                                "error": r["error"]}}, fh, indent=2)
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", required=True)
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--other-token-file", required=True)
    ap.add_argument("--asset", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    tokens = {"own": open(a.token_file).read().strip(), "other organisation": open(a.other_token_file).read().strip()}
    fix = record(a.gateway, a.out, "retrieve", RETRIEVE, {"device": {"assetId": a.asset}}, tokens["own"], "own")
    if fix["status"] != 200:
        print(f"the asset has no position ({fix['status']}): no verification examples", file=sys.stderr)
        return 1
    c = fix["body"]["area"]["center"]
    for case, path, body, which in cases(a.asset, c["latitude"], c["longitude"]):
        r = record(a.gateway, a.out, case, path, body, tokens[which], which)
        b = r["body"] if isinstance(r["body"], dict) else {}
        print(f"{case}: {r['status']} {b.get('code') or b.get('verificationResult') or ''}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

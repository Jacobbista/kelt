#!/usr/bin/env python3
"""verification: one recorded exchange per behaviour of the private-asset
profile, compared with what the running gateway's own contract declares.

Owner: experiments/README.md. Contract authority: GET /contracts/<name> on the
gateway (5g-northbound spec/private-profile).
"""
from __future__ import annotations

import datetime
import json
import urllib.error
import urllib.request


def _declared(contract: dict) -> set[tuple[int, str]]:
    pairs = set()
    for resp in contract.get("components", {}).get("responses", {}).values():
        for ex in resp.get("content", {}).get("application/json", {}).get("examples", {}).values():
            v = ex.get("value", {})
            if "status" in v and "code" in v:
                pairs.add((int(v["status"]), v["code"]))
    return pairs


def expect_error(contract: dict, status: int, code: str) -> bool:
    return (status, code) in _declared(contract)


def run_case(name: str, method: str, url: str, headers: dict, body, timeout: float = 15) -> dict:
    """One exchange. Never raises: a network error is recorded with status 0."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **headers})
    out = {"case": name, "status": 0, "code": None, "body": None, "x_correlator": None, "error": None}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out["status"], raw, hdrs = r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        out["status"], raw, hdrs = e.code, e.read(), e.headers
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    out["x_correlator"] = hdrs.get("x-correlator")
    try:
        out["body"] = json.loads(raw)
        out["code"] = out["body"].get("code") if isinstance(out["body"], dict) else None
    except ValueError:
        out["body"] = raw.decode(errors="replace")[:2000]
    return out


def _row(case, expected, actual, ok):
    return {"case": case, "expected": expected, "actual": actual,
            "verdict": "observed" if ok is None else ("pass" if ok else "fail")}


def check_data(api: dict, adapter: dict, source_class_upper: float, asset_kind: str) -> list[dict]:
    """The API answer against the adapter's measurement of the same fix.

    Only a wgs84 adapter is compared (the venue transform of a local frame is
    not reimplemented here). Altitude is recorded without a verdict until the
    profile states its rule.
    """
    c = api["area"]["center"]
    ts = datetime.datetime.fromtimestamp(int(adapter["timestamp"]), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hacc = adapter.get("horizontalAccuracy", source_class_upper)
    return [
        _row("coordinates", [adapter["latitude"], adapter["longitude"]], [c["latitude"], c["longitude"]],
             adapter.get("frame") == "wgs84" and abs(c["latitude"] - adapter["latitude"]) < 1e-9
             and abs(c["longitude"] - adapter["longitude"]) < 1e-9),
        _row("timestamp", ts, api.get("lastLocationTime"), api.get("lastLocationTime") == ts),
        _row("source", adapter.get("source"), api.get("source"), api.get("source") == adapter.get("source")),
        _row("kind", asset_kind, api.get("kind"), api.get("kind") == asset_kind),
        _row("accuracy", hacc, api.get("horizontalAccuracy"), api.get("horizontalAccuracy") == hacc),
        _row("radius_floor", f"max({hacc}, 1)", api["area"].get("radius"),
             api["area"].get("radius") == max(float(hacc), 1.0)),
        _row("altitude", adapter.get("y"), api.get("altitude"), None),
    ]


# ── campaign ──────────────────────────────────────────────────────────────────
# Driven by run.sh (which mints the token, checks for leftovers and installs the
# EXIT trap that undoes every fault). Cluster actions go through faults.sh and
# lib/common.sh, so this file never talks to kubectl on its own.

import math  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

import yaml  # noqa: E402

EXP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
RETRIEVE = "/location-retrieval/v0.5/retrieve"
VERIFY = "/location-verification/v3/verify"


def _sh(fn: str, *args: str) -> str:
    """Run a faults.sh / common.sh function in bash, return stdout."""
    cmd = f'source lib/common.sh; source exposure/faults.sh; {fn} "$@"'
    r = subprocess.run(["bash", "-c", cmd, "sh", *args], cwd=EXP, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"{fn} failed: {r.stderr.strip()[-300:]}")
    return r.stdout


class Campaign:
    def __init__(self, gw: str, run_dir: str, token: str):
        self.gw, self.run_dir, self.token = gw, run_dir, token
        self.rows: list[dict] = []
        os.makedirs(os.path.join(run_dir, "raw"), exist_ok=True)

    def get_json(self, path: str) -> dict:
        r = run_case("get", "GET", self.gw + path, {"Authorization": f"Bearer {self.token}"}, None)
        return r["body"] if isinstance(r["body"], (dict, list)) else {}

    def call(self, case: str, path: str, body, token_kind: str = "valid", extra: dict | None = None) -> dict:
        headers = dict(extra or {})
        if token_kind == "valid":
            headers["Authorization"] = f"Bearer {self.token}"
        elif token_kind == "bad":
            headers["Authorization"] = "Bearer x"
        r = run_case(case, "POST", self.gw + path, headers, body)
        saved = {"request": {"path": path, "token": token_kind, "headers": {k: v for k, v in (extra or {}).items()},
                             "body": body}, "response": r}
        with open(os.path.join(self.run_dir, "raw", f"{case}.json"), "w") as fh:
            json.dump(saved, fh, indent=2)
        return r

    def record(self, group: str, case: str, expected, actual, verdict: str, note: str = "") -> None:
        row = {"group": group, "case": case, "expected": expected, "actual": actual, "verdict": verdict, "note": note}
        self.rows.append(row)
        print(f"{verdict:>14}  {group}/{case}: expected {expected}, got {actual} {note}".rstrip(), flush=True)


def _status_code(r: dict):
    return [r["status"], r["code"]] if r["status"] else ["error", r["error"]]


def profile_cases(c: Campaign, contract: dict, asset: str, fix: dict) -> None:
    def err(case, body, status, code, token_kind="valid"):
        r = c.call(case, RETRIEVE, body, token_kind)
        ok = r["status"] == status and r["code"] == code and expect_error(contract, status, code)
        c.record("profile", case, [status, code], _status_code(r), "pass" if ok else "fail")
        return r

    sent = "kelt-exp-" + str(int(time.time()))
    r = c.call("asset_id", RETRIEVE, {"device": {"assetId": asset}}, extra={"x-correlator": sent})
    c.record("profile", "asset_id", 200, r["status"], "pass" if r["status"] == 200 else "fail")
    c.record("profile", "x_correlator_echo", sent, r["x_correlator"], "pass" if r["x_correlator"] == sent else "fail")
    err("public_phone", {"device": {"phoneNumber": "+46700000000"}}, 422, "UNSUPPORTED_IDENTIFIER")
    err("no_token", {"device": {"assetId": asset}}, 401, "UNAUTHENTICATED", token_kind="none")
    err("bad_token", {"device": {"assetId": asset}}, 401, "UNAUTHENTICATED", token_kind="bad")
    c.record("profile", "no_role", [403, "PERMISSION_DENIED"], None, "not exercised",
             "no realm client mints a token without camara-location-read (camara-gateway carries the role)")
    err("malformed", {"device": {"assetId": 123}}, 400, "INVALID_ARGUMENT")
    err("unknown_asset", {"device": {"assetId": "kelt-exp-no-such-asset"}}, 404, "IDENTIFIER_NOT_FOUND")

    # Verify, with circles built around the asset's own current fix.
    lat, lon = fix["area"]["center"]["latitude"], fix["area"]["center"]["longitude"]
    one_m_lon = 1.0 / (111320.0 * math.cos(math.radians(lat)))
    areas = {
        "verify_inside": ({"areaType": "CIRCLE", "center": {"latitude": lat, "longitude": lon}, "radius": 50}, "TRUE"),
        "verify_outside": ({"areaType": "CIRCLE", "center": {"latitude": lat + 0.1, "longitude": lon}, "radius": 50}, "FALSE"),
        "verify_partial": ({"areaType": "CIRCLE", "center": {"latitude": lat, "longitude": lon + one_m_lon},
                            "radius": 1}, "PARTIAL"),
    }
    for case, (area, want) in areas.items():
        r = c.call(case, VERIFY, {"device": {"assetId": asset}, "area": area})
        got = r["body"].get("verificationResult") if isinstance(r["body"], dict) else None
        ok = r["status"] == 200 and got == want and (want != "PARTIAL" or "matchRate" in r["body"])
        c.record("profile", case, want, got if got else _status_code(r), "pass" if ok else "fail",
                 f"matchRate={r['body'].get('matchRate')}" if want == "PARTIAL" and isinstance(r["body"], dict) else "")


def data_cases(c: Campaign, asset: str, kind: str, adapter_app: str, class_upper: float) -> None:
    r = c.call("data_retrieve", RETRIEVE, {"device": {"assetId": asset}})
    raw = _sh("kubectl", "exec", "-n", os.environ.get("KELT_POS_NS", "positioning"), "deploy/positioning-engine", "--",
              "python3", "-c",
              "import urllib.request,sys;print(urllib.request.urlopen(sys.argv[1],timeout=15).read().decode())",
              f"http://{adapter_app}:8080/measurement/{asset}")
    with open(os.path.join(c.run_dir, "raw", "data_adapter_measurement.json"), "w") as fh:
        fh.write(raw)
    adapter = json.loads(raw)
    if r["status"] != 200:
        c.record("data", "retrieve", 200, _status_code(r), "fail")
        return
    for row in check_data(r["body"], adapter, class_upper, kind):
        c.record("data", row["case"], row["expected"], row["actual"], row["verdict"],
                 "profile rework pending" if row["verdict"] == "observed" else "")
    r0 = c.call("maxage_zero", RETRIEVE, {"device": {"assetId": asset}, "maxAge": 0})
    age = None
    if isinstance(r0["body"], dict) and r0["body"].get("lastLocationTime"):
        t = datetime.datetime.fromisoformat(r0["body"]["lastLocationTime"].replace("Z", "+00:00"))
        age = round((datetime.datetime.now(datetime.timezone.utc) - t).total_seconds())
    c.record("data", "maxage_zero", "fresh fix or 422 UNABLE_TO_FULFILL_MAX_AGE", _status_code(r0), "observed",
             f"fix age {age} s; profile rework pending")


def _poll(fn, timeout_s: float, every: float = 0.5):
    """Call fn() every `every` s until it returns a truthy value; (value, seconds) or (None, timeout)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        v = fn()
        if v:
            return v, round(time.monotonic() - t0, 2)
        time.sleep(every)
    return None, round(time.monotonic() - t0, 2)


def fault_cases(c: Campaign, contract: dict, asset: str, synthetic: str, adapter_app: str, deploy: str,
                injections: int, timeout_s: float) -> None:
    body = {"device": {"assetId": asset}, "maxAge": 0}

    def declared_422():
        r = run_case("poll", "POST", c.gw + RETRIEVE, {"Authorization": f"Bearer {c.token}"}, body)
        return r if r["status"] == 422 and expect_error(contract, 422, r["code"] or "") else None

    def ok_200():
        r = run_case("poll", "POST", c.gw + RETRIEVE, {"Authorization": f"Bearer {c.token}"}, body)
        return r if r["status"] == 200 else None

    for i in range(1, injections + 1):
        t0 = time.monotonic()
        _sh("fault_block_vendor", adapter_app)
        applied = round(time.monotonic() - t0, 2)
        got, secs = _poll(declared_422, timeout_s)
        _sh("fault_unblock_vendor")
        c.record("fault", f"vendor_unreachable_{i}", "declared 422", got["code"] if got else "no 422",
                 "pass" if got else "fail", f"{secs} s after the policy was applied ({round(secs + applied, 2)} s from the apply call)")
        back, bsecs = _poll(ok_200, timeout_s)
        if not back:
            c.record("fault", f"vendor_recovered_{i}", 200, "no 200", "fail", f"after {bsecs} s")
            return

    def adapter_state():
        a = c.get_json("/adapters")
        for x in a.get("adapters", []):
            if x.get("name") == adapter_app:
                return x.get("state")
        return "absent"

    synth_ok: list[int] = []
    baseline = run_case("baseline", "POST", c.gw + RETRIEVE, {"Authorization": f"Bearer {c.token}"},
                        {"device": {"assetId": synthetic}})

    def gone():
        r = run_case("poll", "POST", c.gw + RETRIEVE, {"Authorization": f"Bearer {c.token}"},
                     {"device": {"assetId": synthetic}})
        synth_ok.append(r["status"])
        return adapter_state() != "live"

    t0 = time.monotonic()
    _sh("fault_scale_down", deploy)
    applied = round(time.monotonic() - t0, 2)
    got, secs = _poll(gone, timeout_s)
    state = adapter_state()
    _sh("fault_restore_scale")
    c.record("fault", "adapter_removed", "not live", state, "pass" if got else "fail",
             f"{secs} s after the scale-down returned ({round(secs + applied, 2)} s from the call)")
    if baseline["status"] != 200:
        c.record("fault", "others_still_served", "synthetic 200 throughout", sorted(set(synth_ok)), "not exercised",
                 f"the synthetic asset answers {_status_code(baseline)} before the fault too (not placed?)")
    else:
        c.record("fault", "others_still_served", "synthetic 200 throughout", sorted(set(synth_ok)),
                 "pass" if synth_ok and all(x == 200 for x in synth_ok) else "fail", f"{len(synth_ok)} polls")
    back, bsecs = _poll(lambda: adapter_state() == "live", max(timeout_s, 180))
    c.record("fault", "adapter_back", "live", adapter_state(), "pass" if back else "fail", f"after {bsecs} s")


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", required=True)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--adapter-app", default="wittra")
    ap.add_argument("--adapter-deploy", default="wittra")
    ap.add_argument("--injections", type=int, default=3)
    ap.add_argument("--fault-timeout", type=float, default=120)
    ap.add_argument("--skip-faults", action="store_true")
    a = ap.parse_args()
    c = Campaign(a.gateway, a.run_dir, os.environ["KELT_CAMARA_TOKEN"])
    contracts = {}
    os.makedirs(os.path.join(a.run_dir, "raw", "contracts"), exist_ok=True)
    for name in ("location-retrieval.profiled.yaml", "location-verification.profiled.yaml",
                 "accuracy-class-vocabulary.json"):
        with urllib.request.urlopen(f"{a.gateway}/contracts/{name}", timeout=15) as resp:
            text = resp.read().decode()
        with open(os.path.join(a.run_dir, "raw", "contracts", name), "w") as fh:
            fh.write(text)
        contracts[name] = yaml.safe_load(text)
    retrieval = contracts["location-retrieval.profiled.yaml"]
    assets = c.get_json("/assets")
    items = assets if isinstance(assets, list) else assets.get("assets", assets.get("items", []))
    by_source = {cap.get("source"): x for x in items for cap in x.get("capabilities", [])}
    vendor, synthetic = by_source.get(a.adapter_app), by_source.get("synthetic")
    if not vendor or not synthetic:
        print("need one asset of the vendor source and one synthetic asset", file=sys.stderr)
        return 2
    caps = c.get_json("/capabilities")
    klass = next((x["capabilities"].get("accuracy_class") for x in caps.get("adapters", []) if x.get("name") == a.adapter_app), None)
    upper = contracts["accuracy-class-vocabulary.json"]["classes"][klass]["upperBound"]
    fix = c.call("fix_for_areas", RETRIEVE, {"device": {"assetId": vendor["assetId"]}})
    if fix["status"] != 200:
        print(f"the vendor asset has no position ({fix['status']}): cannot build the verify areas", file=sys.stderr)
        return 2
    profile_cases(c, retrieval, vendor["assetId"], fix["body"])
    data_cases(c, vendor["assetId"], vendor.get("kind"), a.adapter_app, float(upper))
    if not a.skip_faults:
        fault_cases(c, retrieval, vendor["assetId"], synthetic["assetId"], a.adapter_app, a.adapter_deploy,
                    a.injections, a.fault_timeout)
    with open(os.path.join(a.run_dir, "cases.jsonl"), "w") as fh:
        for row in c.rows:
            fh.write(json.dumps(row) + "\n")
    counts: dict[str, int] = {}
    for row in c.rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    with open(os.path.join(a.run_dir, "summary.json"), "w") as fh:
        json.dump({"counts": counts, "cases": len(c.rows)}, fh, indent=2)
    md = ["# verification", "", "| group | case | expected | actual | verdict | note |", "|---|---|---|---|---|---|"]
    md += [f"| {r['group']} | {r['case']} | {r['expected']} | {r['actual']} | {r['verdict']} | {r['note']} |" for r in c.rows]
    md += ["", "Counts: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))]
    with open(os.path.join(a.run_dir, "summary.md"), "w") as fh:
        fh.write("\n".join(md) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

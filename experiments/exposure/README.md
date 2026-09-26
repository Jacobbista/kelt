# Exposure campaigns: verification and response-time

Both run against the live deployment (gateway, engine and adapters as pods,
phase 10) through the gateway's NodePort, with a token minted for the
`camara-api-demo` client from `.testbed.secrets` (never written to a run).

**Contract authority.** The behaviour the stack must show is the profiled
contract the gateway serves: `GET /contracts/location-retrieval.profiled.yaml`,
`location-verification.profiled.yaml`, `accuracy-class-vocabulary.json`
(generated in 5g-northbound, `spec/private-profile`). The verification campaign
reads them at run time and keeps a copy in the run; expected codes are the pairs
the contract declares, not a list kept here.

## verification

`run.sh verification`, driven by `verification.py`, faults in `faults.sh`.

| Group | Cases |
|---|---|
| Identifiers and authorisation | asset served (200) and `x-correlator` echoed; public identifier → 422 `UNSUPPORTED_IDENTIFIER`; no token and a bad token → 401; malformed request → 400 `INVALID_ARGUMENT`; unknown asset → 404 `IDENTIFIER_NOT_FOUND`; verify inside / outside / partly overlapping the asset's own fix → `TRUE` / `FALSE` / `PARTIAL` with `matchRate` |
| Data | the answer against the adapter's `/measurement/{id}` for the same fix: coordinates (the Wittra adapter reports WGS84), time of the estimate, source, kind, accuracy (the source's own, else its class's upper bound) and `area.radius` = max(accuracy, 1 m) |
| Fault | vendor unreachable, three times: a NetworkPolicy lets the vendor adapter reach only private addresses, and the campaign polls `maxAge=0` every 0.5 s until a 422 the contract declares; adapter removed: scaled to 0, polls the gateway's `/adapters` until it is no longer live, while the synthetic asset must stay served |

Recorded without a verdict, until the new profile proposal is released:
`altitude` and `maxAge=0`. Not exercised: a token without the
`camara-location-read` role (no realm client mints one), and the "others still
served" fault case while the synthetic asset has no position (its device is not
placed).

Every fault is undone by an `EXIT` trap in `run.sh`, also on failure or Ctrl-C;
the campaign refuses to start while a leftover exists (a policy labelled
`app.kubernetes.io/managed-by=kelt-experiments`, or a positioning Deployment at
0 replicas). Fault timings are given twice: from when the `kubectl` call
returned and from when it started (the call goes through the master VM and
takes seconds).

## response-time

`run.sh response-time`: `response_time_driver.sh` sends the requests paced on a
schedule (request *i* at t0 + *i*/rate) and keeps the `x-correlator` of each;
`hop_aggregate.py` joins the hop lines the services log (schema:
`hop-log.schema.json`, vendored from 5g-northbound) into one trace per request,
drops the first `KELT_LAT_WARMUP` traces (default 50) as warm-up, and computes
each service's own time (its span minus the spans it waited on);
`response_time_summary.py` gives median, p90 and p99 per component.

| Condition | Request |
|---|---|
| `hit` | the Wittra asset, no `maxAge` |
| `fresh` | the Wittra asset, `maxAge=0` |
| `local` | the synthetic asset, `maxAge=0`: no external call (needs the demo asset placed) |

The vendor share is the adapter's span on the requests that reached the vendor
(adapter span ≥ 10 ms; from the adapter's cache it is about 0.3 ms). The stack
share is the request's total minus that span, on the same requests. See
`../README.md` for what the deployed version does with caching, which decides
how many requests reach the vendor.

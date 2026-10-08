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
| Data | the answer against the adapter's `/measurement/{id}` for the same fix: coordinates (the Wittra adapter reports WGS84), established time (for an `on_motion` source the later of the fix time and the last communication, else the fix time), source, kind, accuracy (the source's own, else its nominalAccuracy, else its class's upper bound), `area.radius` = max(accuracy, 1 m), `altitude` (the georef's `altitude_m` plus the measured `z`, absent when either is missing), and `maxAge=0` (200 only for a position current by the source's declaration, else 422; the row records the declaration in force) |
| Fault | vendor unreachable, three times: a NetworkPolicy lets the vendor adapter reach only private addresses, and the campaign polls without `maxAge` every 0.5 s until an error the contract declares; adapter removed: scaled to 0, polls the gateway's `/adapters` until it is no longer live, while the synthetic asset must stay served (placed for the group when it has no position, and removed after only if placed there) |

The declaration and the georef are read from the gateway (`GET /adapters`,
`GET /blueprint`). Not exercised: a token without the `camara-location-read`
role (no realm client mints one).

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
`response_time_summary.py` gives median, p90 and p99 per component, per HTTP
status of the request, and counts where each request was answered from its hop
lines: the gateway's cache (no engine line), the vendor-adapter's cache (its
line says `cache: hit`, or, before 0.19.2, its span is under 10 ms), the
vendor, or an adapter with no vendor behind it.

| Condition | Request |
|---|---|
| `hit` | the Wittra asset, no `maxAge`, at `KELT_LAT_RATE` (default 5/s) |
| `fresh` | the Wittra asset, no `maxAge`, `KELT_LAT_FRESH_COUNT` (default 300) at `KELT_LAT_FRESH_RATE` (default 0.15/s), with the asset's other consumers closed |
| `local` | the synthetic asset, `maxAge=0`: no external call; placed for the run when it has no position, and removed after only if placed there |

The vendor share is the adapter's span on the requests that reached the vendor;
the stack share is the request's total minus that span, on the same requests.
See `../README.md` for the caches, which decide how many requests reach the
vendor.

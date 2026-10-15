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

`run.sh response-time`: for every condition but `local50`,
`response_time_driver.sh` sends the requests one after the other, paced on a
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
| `rest` | nothing sent for `KELT_RESOURCE_S` (default 300 s): the footprint at rest. The WiFi adapter writes one hop line per scan ingest (`POST /ingest/wifi-scan`, with its status, northbound 0.19.4); the check counts those inside the window, and the adapter's `/devices` at the end says whether a scanner was located. With `KELT_SCANNER_SSH` set, the scanner's journal over the window is read too (unit `KELT_SCANNER_UNIT`, default `scanner`; messages only), and its scans must match the adapter's lines within one at the edges |
| `hit` | the Wittra asset, no `maxAge`, at `KELT_LAT_RATE` (default 5/s) |
| `fresh` | the Wittra asset, no `maxAge`, `KELT_LAT_FRESH_COUNT` (default 300) at `KELT_LAT_FRESH_RATE` (default 0.15/s) |
| `local` | the synthetic asset, `maxAge=0`: no external call; placed for the run when it has no position, and removed after only if placed there |
| `local50` | `KELT_LAT_LOAD_ASSETS` (default 50) synthetic assets, each asked once a second with `maxAge=0` (`KELT_LAT_LOAD_RATE`, default one request per asset per second), for `KELT_LAT_LOAD_WARMUP_S` (10 s, discarded) and `KELT_LAT_LOAD_S` (60 s) |

The vendor asset's other consumers (location-app map and detail panel,
position-stream clients, dashboards) are closed for the whole campaign: they
keep the adapter's cache warm and they load the stack. `KELT_LAT_CONDITIONS`
selects and orders the conditions; a token is minted for each, since a token
lives one hour and the campaign is longer.

`local50` is sent open loop by `response_time_load.py`: request *i* starts at
t0 + *i*/rate on its own thread, whatever the earlier requests are doing, each
on its own connection. The requests file adds the send time and the delay
behind the schedule of each request; `load.json` keeps the nominal rate. The
load assets come from `load_fixture.sh`: one asset per id (`kelt-load-01` to
`kelt-load-50`, prefix `KELT_LOAD_PREFIX`) is added to the gateway's map with
the operator token (`camara-gateway` client), each with one `synthetic`
capability whose `positioningId` is the asset id, and each is placed on a grid
over the room through `PUT /assets/{id}/placement`. Placing an id the
synthetic adapter does not declare in `DEVICE_IDS` creates that device
(northbound 0.19.3). After the condition, and on exit, the placements are
removed, which ends the devices, and the assets are taken off the map. The
synthetic adapter keeps placements in memory, so it runs as one replica and is
not restarted during the run. The campaign refuses to start while a load asset
is left in the map or a load device in the adapter, or while the adapter has
more than one replica.

The footprint samplers (`../README.md`, Footprint) run from the first
condition to the last; each condition's window starts after its warm-up.
`check_run.py` checks every run: each request answered and traced, every hop
present, every `maxAge=0` request on the synthetic source answered 200 by the adapter,
the open-loop rate at no less than 98% of the nominal and 99% of its requests
within one period of their schedule, and the footprint samples.

The vendor share is the adapter's span on the requests that reached the vendor;
the stack share is the request's total minus that span, on the same requests.
See `../README.md` for the caches, which decide how many requests reach the
vendor.

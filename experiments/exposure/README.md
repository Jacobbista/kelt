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

`run.sh response-time` sends every condition but `rest` open loop with
`response_time_load.py`: request *i* starts at t0 + *i*/rate on its own thread,
whatever the earlier requests are doing, each on its own connection, and the
`x-correlator` of each is kept. Each condition has `KELT_LAT_WARMUP_S` (default
10 s) of warm-up, discarded, then at least `KELT_LAT_MIN_COUNT` (default 1000)
requests and at least `KELT_LAT_MIN_S` (default 60 s). The requests file has
the send time and the delay behind the schedule of each request; `load.json`
keeps the nominal rate and the warm-up count.

The pod logs of the gateway and of every positioning Deployment are followed on
the master VM for each condition (`hoplog_start` and `hoplog_stop` in
`../lib/common.sh`), so the lines survive the kubelet's log rotation.
`hop_aggregate.py` joins the hop lines the services log (schema:
`hop-log.schema.json`, vendored from 5g-northbound) into one trace per request,
drops the warm-up, and computes each service's own time (its span minus the
spans it waited on); `response_time_summary.py` gives median, p90, p99 and
maximum per component, per HTTP status of the request, and counts where each
request was answered from its hop lines: the gateway's cache (no engine line),
the vendor-adapter's cache (its line says `cache: hit`, or, before 0.19.2, its
span is under 10 ms), the vendor, or an adapter with no vendor behind it. It
also counts the requests on either side of 9 ms end to end and keeps the 1 ms
histogram: at low rates the synthetic source's end-to-end time takes two
values, about 5 to 7 ms and 11 to 14 ms, with few requests between.

| Condition | Request |
|---|---|
| `rest` | nothing sent for `KELT_RESOURCE_S` (default 300 s): the footprint at rest. The WiFi adapter writes one hop line per scan ingest (`POST /ingest/wifi-scan`, with its status, northbound 0.19.4); the check counts those inside the window, and the adapter's `/devices` at the end says whether a scanner was located. With `KELT_SCANNER_SSH` set, the scanner's journal over the window is read too (unit `KELT_SCANNER_UNIT`, default `scanner`; messages only), and its scans must match the adapter's lines within one at the edges |
| `wittra-cached` | the Wittra asset, no `maxAge`, at `KELT_LAT_CACHED_RATE` (default 5/s) |
| `wittra-cloud` | the Wittra asset, no `maxAge`, at `KELT_LAT_CLOUD_RATE` (default 0.15/s), past both 5 s caches |
| `synthetic-<rate>` | `KELT_LAT_LOAD_ASSETS` (default 50) synthetic assets, `maxAge=0`, `<rate>` requests per second in total, the assets asked in turn |
| `stream-<clients>` | the same assets and no request: `<clients>` clients on the gateway's `/positions/stream` for `KELT_LAT_WARMUP_S` of warm-up, then `KELT_STREAM_S` (default 300 s) |
| `examples` | the recorded examples (`examples.py`) on the Wittra asset: a verification with a circle of 50 m centred on its last position, one with a circle of 2 m centred 1.5 m east of it, a retrieval naming a phone number, and a retrieval with the token of another organisation; for that one `other_org.sh` creates a temporary service-account client in the realm (attribute `org`, default `other`, role `camara-location-read`) through Keycloak's admin API and deletes it right after |

`KELT_LAT_CONDITIONS` selects and orders the conditions (default `rest
wittra-cached wittra-cloud synthetic-5 synthetic-10 synthetic-25 synthetic-50
synthetic-100 synthetic-200 stream-1 stream-5 examples`).

`stream_client.py` runs the stream clients on this host, one WebSocket each,
with the token offered as the subprotocols `bearer.jwt` and `<jwt>`, as the
gateway expects. The gateway opens one upstream connection to the engine per
client and forwards each broadcast as a list of positions. The engine asks
the adapters for its broadcast only while at least one client is connected,
once per interval for all clients; with no client it sends nothing, so the
other conditions do not include the broadcast. For each client the
condition records the messages and positions per second and the interval
between messages; for each position, its age on arrival: the time of receipt
on this host's clock minus the position's `timestamp`, the time of its fix.
`stream.json` also records the engine's broadcast interval
(`WEBSOCKET_INTERVAL_MS` in the engine's ConfigMap). The fix times are set in
the cluster: every synthetic condition bounds the offset between the
gateway's clock and this host's from its requests and the gateway's hop lines
(`clock_offset.py`, `clock.json`), which bounds the error of each age.

The vendor asset's other consumers (location-app map and detail panel,
position-stream clients, dashboards) are closed for the whole campaign: they
keep the adapter's cache warm and they load the stack. The token sits in a
file only the user reads, renewed every 20 minutes, since a token lives one
hour and `wittra-cloud` takes about two.

The load assets come from `load_fixture.sh`: one asset per id
(`kelt-load-01` to `kelt-load-50`, prefix `KELT_LOAD_PREFIX`) is added to the
gateway's map with the operator token (`camara-gateway` client), each with one
`synthetic` capability whose `positioningId` is the asset id, and each is
placed on a grid over the room through `PUT /assets/{id}/placement`. Placing
an id the synthetic adapter does not declare in `DEVICE_IDS` creates that
device (northbound 0.19.3). They are added before the first synthetic or
stream condition; after the last, and on exit, the placements are removed, which ends
the devices, and the assets are taken off the map. The synthetic adapter keeps
placements in memory, so it runs as one replica and is not restarted during
the run. The campaign refuses to start while a load asset is left in the map
or a load device in the adapter, or while the adapter has more than one
replica.

The footprint samplers (`../README.md`, Footprint) run from the first
condition to the last; each condition's window starts after its warm-up.
`check_run.py` checks every run: each request answered and traced, every hop
present, every `maxAge=0` request on the synthetic source answered 200 by the
adapter, each condition sent at no less than 98% of its nominal rate with 99%
of its requests within one period of their schedule, every stream client
connected for the whole window at no less than 98% of the broadcast rate and
never more than 2 s without a message, the clock bound, and the footprint
samples.

From northbound 0.19.6 the vendor-adapter's hop line on a cache miss also carries `connection` (`new` or `reused`) and `upstream_ms` (from the request sent to the vendor to its response headers: the vendor's time plus the round trip); the summary reports `upstream_ms`, the rest of the adapter's span (its own work and any connection setup) and the count of new and reused connections.

The vendor share is the adapter's span on the requests that reached the vendor;
the stack share is the request's total minus that span, on the same requests.
See `../README.md` for the caches, which decide how many requests reach the
vendor.

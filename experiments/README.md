# experiments/

Measurements against the live testbed: the real 5G core, the deployed
northbound stack (phase 10), the real vendor cloud. Nothing here uses a mock;
the numbers describe the deployment as it runs. The campaigns are named after
the sections of the thesis that report them.

## Run

```bash
experiments/run.sh resource-use idle                  # 5 min at rest
experiments/run.sh resource-use from <run-dir>...     # the windows other runs recorded
experiments/run.sh verification                        # one exchange per profile behaviour
experiments/run.sh response-time                       # where a location request's time goes
experiments/run.sh report                              # every run, one line each
python3 experiments/tables.py <campaign>               # the thesis table of a campaign
```

`throughput` and `rtt` (the network campaigns) are not here yet: they run on
the Raspberry Pi UE and wait for how its traffic is isolated to be settled.
The old probe plans for them are in `network/`.

`KELT_PILOT=1` marks a trial run: it is recorded like any other and never
reaches the tables. Other knobs are environment variables listed at the top of
each campaign function in `run.sh`.

## What each one measures

| Campaign | Thesis | Question | How |
|---|---|---|---|
| `resource-use` | 4.10.2, 5.11.3 | CPU and memory the core and the exposure stack take | Prometheus (cAdvisor) per pod over 5 min windows: at rest, and over the windows the other campaigns recorded, so the load is the one their tables report. Groups: core (`5g`), exposure (`positioning`, `camara`), identity (`iam`), the mec measurement server, diagnostic probes (`netshoot`, wherever it lives); anything else is listed as other |
| `verification` | 5.11.1 | Does the running stack behave as the private-asset profile specifies | One recorded exchange per case, compared with the contract the gateway itself serves (`GET /contracts/<name>`): identifiers and authorisation, data carried from the adapter, faults (vendor unreachable, adapter removed) |
| `response-time` | 5.11.2 | How long a location request takes and how it divides between components and the vendor | 1000 requests per condition at 5/s; every service logs one hop line per request, joined by `x-correlator` |

## A run directory

`runs/<campaign>/<utc>/` (not committed):

- `provenance.json`: date, KELT commit and whether the tree had changes, the
  5g-northbound revision, the two deployment flags read from the cluster, the
  image of every deployment, `pilot`;
- `window.json`: the measured windows (UTC and local) and any discarded runs;
- `raw/`: tool output as produced (Prometheus answers, exchanges, contracts,
  pod logs);
- `summary.json`, `summary.md`: the numbers of the run.

`runs/_tables/` holds what `tables.py` builds: every non-pilot run of a
campaign, or only the runs listed in `thesis-runs.txt` when that file exists.

## Things to know when reading the numbers

- The adapter keeps the vendor's answer for `cacheTtl` seconds (default 5), and
  `maxAge=0` does not bypass that cache on the deployed version. At 5
  requests/s about one request in 25 reaches the vendor cloud; the
  response-time summary counts them and computes the stack share on those only.
- There is no hop line for the adapter's own call to the vendor: the vendor
  share is the adapter's span, which includes the adapter's own processing
  (about 0.3 ms).
- The gateway has no cache of its own on the deployed version: a request
  without `maxAge` crosses the engine and the adapter like one with
  `maxAge=0`.
- Latency seen from the testbed host carries a VirtualBox artefact on
  requests larger than one segment
  ([known issue](../docs/known-issues/virtualbox-hostonly-tso.md)); the
  per-component numbers come from the services' own logs and are not affected.

## Layout

```
run.sh                 entry point: checks, campaign, summary
report.sh              one line per recorded run
tables.py              thesis tables from the chosen runs
provenance.sh          what was measured, read from the live deployment
lib/common.sh          cluster access; addresses and names read from all.yml or the cluster
lib/stats.py           the one percentile method every table uses
lib/runmeta.py         measured windows and discarded runs (window.json)
resource-use/          Prometheus queries and grouping
exposure/              verification cases, fault injections, response-time driver and aggregation
network/               probe plans for throughput and rtt (part B)
tests/                 unit tests: python3 -m unittest discover -s experiments/tests -t experiments
runs/                  one directory per run, never overwritten, not committed
```

`exposure/README.md` has the details of the two exposure campaigns.

# experiments/

Measurements against the live testbed: the 5G core as deployed, the northbound
stack of phase 10, the vendor cloud. Nothing here uses a mock. Each campaign is
named after the section of the thesis that reports it, and answers one question.

## Campaigns

| Campaign | Thesis | Question |
|---|---|---|
| `throughput` | 4.10.1.1 | What goodput does the cellular link give an application, towards the edge? |
| `rtt` | 4.10.1.2 | What is the delay on the link, how much of it is spent in the core, and how does it grow under uplink load? |
| `resource-use` | 4.10.2, 5.11.3 | How much CPU and memory do the core and the exposure stack take, at rest and under each load? |
| `verification` | 5.11.1 | Does the running stack behave as the private-asset profile specifies? |
| `response-time` | 5.11.2 | How long does a location request take, and how does the time divide between components and the vendor? |

## Network campaigns

Both measure between the UE and the measurement server, a pod in the apps
namespace on N6m (`apps_measurement_server_n6m_ip` in
`ansible/group_vars/all.yml`, deployed by phase 12) that runs an iperf3 server
and answers ping. Its path is the edge application's: radio, N3, the UPF, N6m.
A UE reaches it from its `internet` session, routed by the UPF without NAT. It
runs in a pod of its own, apart from the UPF it measures through, and serves
about 45 Gbit/s from the UPF pod, far above the radio. The UE is a host reached
over SSH that sends its traffic through the 5G link.

### throughput

Four combinations, each isolating one factor:

| Run | Direction | TCP streams | What it shows |
|---|---|---|---|
| `dl1` | downlink (server to UE) | 1 | what one application flow gets downstream |
| `dl4` | downlink | 4 | how close several flows get to the link capacity |
| `ul1` | uplink (UE to server) | 1 | what one application flow gets upstream |
| `ul4` | uplink | 4 | the uplink capacity |

Each run lasts 60 s. The four combinations run in turn (`dl1 dl4 ul1 ul4`, then
again), five rounds, with 10 s between runs: 20 runs, about 25 minutes. Running
in rounds instead of five runs of one combination in a row spreads a slow change
in radio conditions over all four.

Recorded for each run:

- iperf3's report every 0.1 s at the receiver: the UE for downlink, the
  server for uplink (the sender only knows what it wrote to its socket);
- the UE's link counters before and after, and the UE's CPU every second;
- the worker's RAN NIC counters every second;
- a capture on `br-ran` on the worker (headers only), from which the summary
  counts, per direction, the TCP segments, their payload and the retransmitted
  segments seen from the core.

Reported per combination: median and p90 of the goodput over 1 s windows, the
mean of each run and the spread of the run means, and the 0.1 s series of the
run closest to the median, for a figure. The first 10 s of every run are left
out: the uplink climbs to its level over 8-10 s in every run
([known issue](../docs/known-issues/radio-path-throughput.md)).

### rtt

Two conditions:

| Run | Condition | What it shows |
|---|---|---|
| `idle` | ping only, 10 per second | the delay of the link at rest, and where it is spent |
| `load` | ping while iperf3 saturates the uplink with 4 streams, from 10 s before the ping to 10 s after | how the delay grows when the uplink is full, and where |

Each run lasts 300 s (3000 pings). Three `idle` runs come first, then three
`load` runs, with 10 s between runs: about 35 minutes.

During every run the worker captures the echoes at five points, in the order
a request crosses them (the reply crosses them backwards), keeping only the
echoes (a kernel filter: under load the rest is most of the traffic):

| Point | Where | What it sees |
|---|---|---|
| `br-ran` | the worker's RAN bridge | GTP-U from and to the gNB |
| `br-n3` | the worker's N3 bridge | the same GTP-U, routed by the worker |
| `upf-n3` | the UPF's N3 veth | GTP-U entering and leaving the UPF |
| `upf-n6m` | the UPF's N6m veth | the plain packet leaving and entering the UPF |
| `server` | the measurement server's N6m veth | the plain packet at the server |

The veths are found from each pod's `iflink`; the UPF is the pod holding its
N3 address (`upf_cloud_n3_ip`). All five are on the worker's clock, so each
part is a difference on one clock and no synchronisation is needed:

- **core** = the reply at `br-ran` minus the request at `br-ran`: everything
  past the worker's RAN bridge;
- **access** = the UE's round-trip time minus core for the same request:
  the UE, its router and modem, the radio, the gNB, the cable to the worker
  (radio and gNB processing cannot be separated);
- core in parts, each the request's time from one point to the next plus the
  reply's time back: **worker** (routing RAN to N3), **ovs_n3** (switching to
  the UPF), **upf**, **ovs_n6m** (switching to the server), and **server**
  (its own turnaround).

The `idle` runs also have a full capture on `br-ran`, for the other traffic
(below).

The captures are in nanoseconds (the switching steps are a few microseconds)
and each keeps tcpdump's own report next to it (`<capture>.log`: packets
captured, dropped by the kernel). ping reports three significant digits, so the
total and access resolve 0.1 ms; the parts of the core resolve 0.1 us.

Reported per condition: median, p90, p99 and maximum of the total delay, of
core and access, and of each part of the core; loss per run; every sample with
its parts, for a distribution plot.

### How a network run executes

1. The runner checks the UE (reachable, tools present, the server's ping and
   iperf3 reachable from it, no earlier job left) and records the setup in `ue.json`.
2. It starts the worker's counter sampler, the captures, and the footprint
   samplers (below), copies the job
   (`network/ue-run.sh` and the list of runs) to `/tmp/kelt-run-<campaign>-<stamp>-<attempt>/`
   on the UE, starts it as a transient systemd unit and disconnects. No SSH
   session is open while the job measures.
3. The job pauses the units listed in `KELT_UE_PAUSE_UNITS` for its duration.
   They start again at the end, on a signal, or, if the job dies, through a
   systemd timer set before they are stopped. Nothing on the UE's networking
   changes.
4. Every tool runs under its own timeout; a run that fails or hangs is recorded
   as failed and the job goes on. The whole job has a deadline.
5. After the expected end the runner fetches the job's files, checks them
   against the job's manifest, removes the job from the UE, and writes the
   summary.

A run that did not finish (the runner stopped, the link dropped) is continued
with `run.sh resume`: it fetches what the job left, then runs only the runs no
attempt finished, as a new attempt of the same run. A run cut halfway is
redone whole. `run.sh stop` ends a job on the UE and keeps what it measured.

The UE needs an SSH key login, passwordless sudo (systemd-run, pausing units),
iperf3 and ping. Nothing is installed on it.

## Exposure and resource campaigns

- `resource-use`: the footprint of the testbed at rest, 300 s with nothing
  running (`KELT_RESOURCE_S`). The footprint under load is recorded by the
  campaigns that load the testbed, per condition (`dl1` to `ul4`, `idle`,
  `load`), from the same samplers: `lib/footprint-sampler.sh` once a second on
  both VMs (every pod's cgroup, and the VM) and on the host (the machine). CPU
  is the usage between two samples over their time apart, so its maximum is the
  1 s peak; memory is the working set (memory in use for a machine). Levels:
  the host, each VM, each pod, and pod groups: core (`5g`), exposure
  (`positioning`, `camara`), identity (`iam`), the mec measurement server,
  diagnostic probes (`netshoot`), other. A group is the sum of its pods on one
  1 s grid (the samplers of different machines are not aligned). The sampler
  itself takes about 20 millicores on the worker.
- `verification`: one recorded exchange per case, compared with the contract
  the gateway itself serves (`GET /contracts/<name>`): identifiers and
  authorisation, data carried from the adapter, faults (vendor unreachable,
  adapter removed).
- `response-time`: 1000 requests per condition at 5 per second; every service
  logs one hop line per request, joined by `x-correlator`.

`exposure/README.md` has the details of the two exposure campaigns.

## Running

```bash
KELT_UE_SSH=<user>@<host> experiments/run.sh throughput
KELT_UE_SSH=<user>@<host> experiments/run.sh rtt
KELT_UE_SSH=<user>@<host> experiments/run.sh resume [<run-dir>]   # continue a network run that did not finish
KELT_UE_SSH=<user>@<host> experiments/run.sh stop                 # end a network job, keep what it measured
experiments/run.sh resource-use idle
experiments/run.sh verification
experiments/run.sh response-time
experiments/run.sh report                                         # every run, one line each
python3 experiments/tables.py <campaign>                          # the thesis table of a campaign
```

| Variable | Default | Meaning |
|---|---|---|
| `KELT_UE_SSH` | none | the UE to measure from (network campaigns) |
| `KELT_NET_REPEATS`, `KELT_NET_RUN_S` | 5, 60 | throughput rounds and seconds per run |
| `KELT_RTT_REPEATS`, `KELT_RTT_S` | 3, 300 | rtt runs per condition and seconds per run |
| `KELT_UE_PAUSE_UNITS` | none | systemd units stopped on the UE for the job (an application on the UE that sends over the link) |
| `KELT_CAPTURE` | 1 | 0 leaves out every capture on the worker (the check that capturing does not change the measurement); recorded in `ue.json` |
| `KELT_PILOT` | 0 | 1 marks a trial run: recorded, never in the tables |

The exposure and resource campaigns list their variables at the top of each
campaign function in `run.sh`.

## A run directory

`runs/<campaign>/<utc>/`, never overwritten, not committed:

| Path | Content |
|---|---|
| `provenance.json` | date, KELT commit and whether the tree had changes, the 5g-northbound revision, the deployment flags and every image, read from the cluster; `pilot` |
| `window.json` | the measured windows (UTC and local) and what was discarded, with the reason |
| `summary.json`, `summary.md` | the numbers of the run |
| `raw/` | tool output as produced |
| `ue.json` | network campaigns: the UE, its link and mode, the TCP congestion control of both senders, PDU sessions and gNB UEs at the start and end of each attempt |
| `schedule.txt`, `job/<attempt>/` | network campaigns: the full list of runs, and what each attempt ran |
| `raw/ue/<attempt>/` | per run the tool output and a `.meta` line (start, end, exit code, link counters); the UE's CPU; the job's status and manifest |
| `raw/worker/<attempt>/` | the RAN NIC counters (`ran-nic.csv`), the full `br-ran` capture (`br-ran.pcap.gz`), and for rtt the echoes at the five points (`points/<point>.pcap.gz`) |
| `raw/footprint/<attempt>/` | the 1 s samples of each machine (`master`, `worker`, `host`, `.txt.gz`) and the pod map (`pods.tsv`) |
| `footprint.json`, `footprint.md` | CPU and memory per condition: machines, groups, pods |
| `series-<combination>.csv`, `rtt-samples.csv` | the series and samples for figures |

`runs/_tables/` holds what `tables.py` builds: every non-pilot run of a
campaign, or only the runs listed in `thesis-runs.txt` when that file exists.

## Reading the numbers

- **Why 1 s windows.** A TCP flow delivers in bursts and stalls for longer than
  0.1 s while it recovers a lost segment (the retransmission timeout is at least
  200 ms on Linux). A 0.1 s sample then reads zero, so the median of 0.1 s
  samples describes how bursty the flow is, not the rate the application gets.
  A 1 s window spans tens of round trips and the stalls within them. The 0.1 s
  data stays in the run, and any other window can be computed from it.
- **Link bytes and tool bytes** are reported side by side: the link also
  carries headers, acknowledgements and retransmissions, so their difference is
  protocol overhead, not other traffic.
- **Retransmissions seen on `br-ran`.** Downlink: a retransmitted segment seen
  there means the first copy was lost after the worker (radio side). Uplink: the
  first copy was lost after the worker, inside the testbed. A loss before the
  worker does not appear as a retransmission there.
- **Other traffic.** The rtt capture counts, inside each `idle` run, the packets
  to or from the UE's PDU session address that are not with the server, and their
  bytes per second. SSH to the UE, if its path uses the 5G link, is part of it;
  the job keeps SSH closed while it measures. What can delay a ping is a
  foreign packet queued ahead of it, so the limit is on bytes: 1000 B/s per
  run. One 1500 B packet takes 0.34 ms on a 35 Mbit/s uplink, and at 1000 B/s
  at most one arrives every 1.5 s, reaching fewer than 1 ping in 15. The rtt
  table names the idle runs over the limit.
- **The UE setup** changes the numbers and is recorded in `ue.json`: `mode` is
  `nat` when the UE sits behind a router with the modem, `direct` when the modem
  is in the UE and its address is a PDU session address. A single TCP flow is
  limited by its sender's congestion control: the server pod's for downlink, the
  UE's for uplink. A UE whose Ethernet sits on USB 2.0 cannot exceed about
  300 Mbit/s.
- The vendor adapter keeps the vendor's answer for `cacheTtl` seconds
  (default 5), and `maxAge=0` does not bypass that cache on the deployed
  version. At 5 requests per second about one request in 25 reaches the vendor
  cloud; the response-time summary counts them and computes the stack share on
  those only.
- There is no hop line for the adapter's own call to the vendor: the vendor
  share is the adapter's span, which includes the adapter's own processing
  (about 0.3 ms).
- The gateway has no cache of its own on the deployed version: a request
  without `maxAge` crosses the engine and the adapter like one with `maxAge=0`.
- Latency seen from the testbed host carries a VirtualBox artefact on requests
  larger than one segment
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
network/ue-run.sh      the job that runs on the UE
network/campaign.sh    the runner side of the network campaigns (run.sh sources it)
network/throughput.py  throughput summary
network/rtt.py         rtt summary and the core/access split
network/pcap.py        reading the worker's captures (GTP-U and plain)
network/segments.md    how the round trip divides into segments
network/latency-segments.sh  the intra-cluster legs of the round trip, from a probe pod
resource-use/          footprint.py: CPU and memory per condition from the 1 s samples
lib/footprint-sampler.sh  the 1 s sampler (VMs and host)
exposure/              verification cases, fault injections, response-time driver and aggregation
tests/                 unit tests: python3 -m unittest discover -s experiments/tests -t experiments
                       and bash experiments/tests/{network,lib}/*.test.sh
runs/                  one directory per run, never overwritten, not committed
```

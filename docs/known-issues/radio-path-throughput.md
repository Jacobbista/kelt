# Throughput on the 5G radio path: an uplink ramp and two downlink levels

## Symptom

Measured with the `throughput` campaign on 2026-09-28
([experiments/README.md](../../experiments/README.md)): a UE behind a 5G
router (NAT mode), iperf3 against the server in the UPF pod at the UE anchor,
five runs of 60 s per combination, statistics over 1 s windows after the
first 10 s of each run.

| Combination | Median Mbit/s | p90 | Run means |
|---|---|---|---|
| downlink, 1 stream | 129 | 133 | 72-130 |
| downlink, 4 streams | 128 | 134 | 77-130 |
| uplink, 1 stream | 35.7 | 35.7 | 35.6-35.7 |
| uplink, 4 streams | 35.6 | 37.9 | 34.7-35.4 |

- **Uplink:** every run starts near 6 Mbit/s and climbs almost linearly to
  about 35 Mbit/s over 8-10 s, then stays there.
- **Downlink:** two levels. Every run starts low (10-30 Mbit/s) and moves to
  about 130 Mbit/s in one step, after 2 to 35 s; one run stayed at the low
  level for 35 of its 60 s. One TCP stream and four behave the same.
- The UE's CPU stays under 13% at 130 Mbit/s.

## Cause

Not identified. The measurements rule out:

- **TCP slow start:** at the measured round trip (12.8 ms median at rest) it
  takes well under a second, and four streams step up exactly like one.
- **The UE's CPU.**
- **DRX sleep during a transfer:** the `rtt` idle runs (10 pings per second)
  have a p99 of 22 ms, with no 200-300 ms gaps.

What remains is the radio side: how the gNB schedules and adapts the link
(uplink grant sizes, the bandwidth or modulation used downlink). The gNB's own
counters over the same window would tell which; they were not recorded.

## Consequence for measurements

- The `throughput` campaign runs for 60 s and leaves out the first 10 s of
  every run, the length of the uplink ramp.
- The downlink is reported as a distribution over 1 s windows (median, p90,
  the spread of the run means). A single mean over a run that spent part of
  its time at each level describes neither level.
- An iperf3 run of 20 s or less reports mostly the uplink ramp, or only the
  downlink's low level.

## Where the TCP server goes

Measure against a server on the data path:

- the iperf3 server in each UPF pod, at the UE anchor
  (`ue_internet_gateway`, `10.45.0.1` for DNN `internet`; `ue_mec_gateway`,
  `10.46.0.1` for DNN `mec`);
- the edge endpoint, the measurement server in `mec` on N6m
  ([edge-apps.md](../architecture/edge-apps.md)).

A host outside the planes, such as the testbed host on the host-only network,
cannot be reached from a UE: the plane filter drops that traffic
([plane-isolation.md](../architecture/plane-isolation.md)).

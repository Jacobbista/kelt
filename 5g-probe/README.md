# 5G UE Probe Dashboard

Host-side Flask-SocketIO app for isolating USB or WWAN UE interfaces in Linux network namespaces, opening management tunnels, running iperf3 and ping benchmarks, and managing experiment plans.

## Features

- **Isolation**: DHCP (USB Ethernet) or restored PDU addressing (WWAN) inside auto-named netns (`ue1`, …)
- **Diagnostics**: Subnet, gateway, MTU hints, `topology_hint`, and management UI candidates on host and per-namespace status
- **Web UI tunnel**: localhost HTTP proxy that rewrites `Host:` and forwards to the modem inside the netns (upstream leg uses `socat`)
- **Terminal**: `POST /api/open_netns_terminal` opens a graphical shell in the netns when `DISPLAY` and a terminal emulator are available
- **Quick benchmark**: Blocking ping plus short iperf3 DL and UL (`parallel_streams` optional)
- **Live benchmark**: Streaming iperf3 with configurable `-P`, `-i`, UDP bitrate per direction (sequential DL or UL), and UDP payload mode (`fixed`, `omit`, `auto`)
- **Planner**: Templates live under **`plan_templates/`** (built-ins under **`plan_templates/defaults/`**). Run outputs live under **`results/plan_runs/<slug>/`**. Built-ins **`standard_iperf_smoke`** and **`standard_iperf_smoke_my`** are read-only in the UI (**Duplicate** only); plans you save get **Edit** / **Delete**.

## Quick Start

From the `5g-probe` directory:

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./run-probe.sh
```

`run-probe.sh` runs `sudo` with **`venv/bin/python` by absolute path**, so dependencies resolve correctly even though `sudo` drops the activated venv from `PATH`.

Use **`./run-probe.sh -h`** for launcher flags (**`-d`** / **`--debug`** for tunnel traces on stderr). After **`--`**, remaining args go to **`python -m probe`**.

Alternative without the helper script:

```bash
./run-probe.sh --debug          # tunnel debug (same idea without invoking sudo/python yourself)
sudo ./venv/bin/python -m probe
```

Application code lives in the **`probe/`** package at this repo path; `run-probe.sh` only starts `python -m probe` under `sudo`.

Open **http://localhost:5000**.

### Benchmark targets (presets vs any IP)

Default filler for target fields is **`10.45.0.1`** (**UPF-Cloud** anchor); override default with **`FIVEG_PROBE_UPF_TARGET`**.

**`FIVEG_PROBE_MEC_IPERF_TARGET`** adds a second named preset (post-UPF decapsulated iperf) to the UI datalist and to **`benchmark_targets`** in **`GET /api/config`**.

Those entries are **shortcuts only**: every benchmark accepts **any reachable IP** you type. Nothing is mutually exclusive—you can run toward UPF, then toward MEC, then toward another lab IP in separate runs.

**Named presets you define** in the Web UI (**Run Queue** → Save IP preset) are stored in **`results/user_benchmark_targets.json`** on the probe host and merged into the same datalists and dropdowns (up to 64 entries). Remove them from the list under the save button or via **`DELETE /api/user_benchmark_targets/<id>`** (user-defined ids only).

For route MTU hints inside netns, use **`FIVEG_PROBE_ROUTE_PROBE`** when the default probe address is wrong (defaults to the UPF target).

## API (overview)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/config` | Default `target_ip`, merged `benchmark_targets`, and `user_benchmark_targets` |
| POST | `/api/user_benchmark_targets` | `{"label","ip"}` — save a named preset |
| DELETE | `/api/user_benchmark_targets/<id>` | Remove a user preset (`user_…` id only) |
| GET | `/api/status` | Interfaces and namespaces with diagnostics |
| POST | `/api/isolate` | `interface`, optional `management_host`, `management_port` |
| POST | `/api/reset` | `namespace` |
| POST | `/api/benchmark` | `namespace`, `target_ip`, optional `parallel_streams` |
| POST | `/api/open_netns_terminal` | `namespace` |

### SocketIO

| Event | Direction | Notes |
|-------|-----------|--------|
| `start_live_benchmark` | Client→Server | Extended payload: `parallel_streams`, `interval_s`, `bandwidth_dl`, `udp_length_mode`, `udp_mtu_clamp`, … |
| `stop_live_test` | Client→Server | Stops live run |
| `iperf_data` | Server→Client | Interval samples |
| `ping_data` | Server→Client | ICMP samples |
| `test_complete` | Server→Client | Run finished |

See [docs/tools/5g-probe.md](../docs/tools/5g-probe.md) for full detail.

# 5G UE Probe Dashboard

A real-time web app for managing and benchmarking 5G UE (User Equipment) interfaces using Linux network namespaces and Flask-SocketIO.

## Features

- **Isolate**: Move USB dongles into network namespaces with DHCP + socat WebUI tunnel
- **Interface Fingerprinting**: Detects Realtek (router/uplink) vs UE dongle by MAC OUI
- **Auto-naming**: Namespaces are auto-assigned (`ue1`, `ue2`, `ue3`...)
- **Quick Benchmark**: Blocking ping + iperf3 DL/UL for instant metrics
- **🔴 Live Benchmark**: Real-time iperf3 + ping streaming via SocketIO with animated Chart.js graphs (Ookla-style)
- **WebUI Tunnel**: socat-based access to dongle management page (192.168.1.1:80)

## Quick Start

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
sudo $(which python3) app.py
```

Open **http://localhost:5000**

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/status` | Interfaces + namespaces |
| POST | `/api/isolate` | `{interface}` — auto-isolate |
| POST | `/api/reset` | `{namespace}` — cleanup |
| POST | `/api/benchmark` | `{namespace, target_ip}` — quick test |

### SocketIO Events

| Event | Direction | Description |
|-------|-----------|-------------|
| `start_live_benchmark` | Client→Server | `{namespace, target_ip, duration, mode}` |
| `stop_live_benchmark` | Client→Server | Kill running test |
| `iperf_data` | Server→Client | `{mbps, second}` per interval |
| `ping_data` | Server→Client | `{ms, seq}` per packet |
| `benchmark_complete` | Server→Client | Test finished |

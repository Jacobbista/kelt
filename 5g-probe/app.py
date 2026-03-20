"""
5G UE Probe — Flask-SocketIO Backend (V4)

REST APIs for infrastructure:
- /api/status, /api/isolate, /api/reset, /api/benchmark (quick)
- /api/plans  (plan management CRUD)

SocketIO events for live streaming:
- start_live_benchmark → streams iperf_data in real-time
- start_live_ping       → streams ping_data in real-time
- start_plan / stop_plan → sequential experiment execution with progress events

Must be run as root (sudo).
"""

from __future__ import annotations

import base64
import copy
import csv
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config["SECRET_KEY"] = "5g-probe-secret"
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

# Directory where test results (screenshots + metadata + CSV) are saved
RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
PLANS_DIR = os.path.join(RESULTS_DIR, "plans")
os.makedirs(RESULTS_DIR, exist_ok=True)
os.makedirs(PLANS_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
UE_WEBUI_BASE_PORT = 18180

KNOWN_OUI: Dict[str, str] = {
    "00:e0:4c": "Realtek",
    "0c:5b:8f": "Realtek",
    "48:5d:60": "Realtek",
}

# Active benchmark processes (for stop support)
_active_procs: Dict[str, List[subprocess.Popen]] = {}
_active_lock = threading.Lock()

# Plan concurrency guard
_plan_running = threading.Event()
_plan_sid: Optional[str] = None

# Run queue
_run_queue: List[dict] = []   # [{"plan_name": str, "repeat": int}]
_queue_delay_s: int = 5
_queue_lock = threading.Lock()
_queue_running = threading.Event()


# ---------------------------------------------------------------------------
# BenchmarkSession — carries parameters for a single benchmark run
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkSession:
    sid: str
    ns: str
    target_ip: str
    proto: str
    bandwidth: str
    length_bytes: int = 1200
    second_counter: Dict[str, float] = field(default_factory=lambda: {"val": 0.0})


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _run(cmd: List[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, check=check, text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )


def _run_capture(cmd: List[str]) -> str:
    r = _run(cmd, check=False, capture=True)
    return (r.stdout or "").strip()


def _run_shell(cmd: str) -> str:
    try:
        return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT).strip()
    except subprocess.CalledProcessError as exc:
        return exc.output.strip() if exc.output else str(exc)


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------

def _get_mac(iface: str) -> str:
    try:
        with open(f"/sys/class/net/{iface}/address") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def _identify_vendor(mac: str) -> str:
    return KNOWN_OUI.get(mac[:8].lower(), "Unknown")


def _get_iface_ip4(iface: str) -> Optional[str]:
    """Return the first IPv4 address/prefix currently on an interface, e.g. '10.45.0.3/29'."""
    out = _run_capture(["ip", "-4", "addr", "show", iface])
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+/\d+)", out)
    return m.group(1) if m else None


def _get_default_gw(iface: str) -> Optional[str]:
    """Return the default gateway reachable via a given interface, if any."""
    out = _run_capture(["ip", "route", "show", "dev", iface])
    # Look for 'default via X.X.X.X'
    m = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None


def list_usb_ifaces() -> List[Dict[str, str]]:
    """List isolatable host interfaces: USB Ethernet dongles (enx*) and WWAN modems (wwan*)."""
    out = _run_capture(["ip", "-br", "link", "show"])
    ifaces: List[Dict[str, str]] = []
    for line in out.splitlines():
        parts = line.split()
        if not parts:
            continue
        name = parts[0]
        state = parts[1] if len(parts) > 1 else "UNKNOWN"

        if name.startswith("enx"):
            mac = _get_mac(name)
            vendor = _identify_vendor(mac)
            role = "router" if vendor == "Realtek" else "ue"
            ifaces.append({"name": name, "state": state, "mac": mac,
                           "vendor": vendor, "role": role, "iface_type": "usb"})

        elif name.startswith("wwan"):
            mac = _get_mac(name)
            current_ip = _get_iface_ip4(name)
            gw = _get_default_gw(name)
            ifaces.append({"name": name, "state": state, "mac": mac,
                           "vendor": "WWAN Modem", "role": "ue",
                           "iface_type": "wwan",
                           "current_ip": current_ip or "",
                           "gateway": gw or ""})

    return sorted(ifaces, key=lambda x: x["name"])


def list_netns() -> List[str]:
    out = _run_capture(["ip", "netns", "list"])
    nss: List[str] = []
    for line in out.splitlines():
        parts = line.split()
        if parts:
            nss.append(parts[0].strip())
    return sorted(nss)


def list_ns_ifaces(ns: str) -> List[str]:
    out = _run_capture(["ip", "-n", ns, "-br", "link", "show"])
    ifaces: List[str] = []
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0] != "lo":
            ifaces.append(parts[0])
    return sorted(ifaces)


def next_namespace_name() -> str:
    existing = list_netns()
    idx = 1
    while f"ue{idx}" in existing:
        idx += 1
    return f"ue{idx}"


def webui_port_for_namespace(ns: str) -> int:
    nss = list_netns()
    try:
        idx = nss.index(ns)
    except ValueError:
        idx = len(nss)
    return UE_WEBUI_BASE_PORT + idx


def start_webui_tunnel(ns: str) -> Optional[int]:
    if not shutil.which("socat"):
        return None
    port = webui_port_for_namespace(ns)
    _run(["pkill", "-f", f"netns exec {ns} socat"], check=False)
    subprocess.Popen(
        ["socat", f"TCP-LISTEN:{port},reuseaddr,fork",
         f"EXEC:ip netns exec {ns} socat STDIO TCP\\:192.168.1.1\\:80"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return port


def stop_webui_tunnel(ns: str) -> None:
    _run(["pkill", "-f", f"netns exec {ns} socat"], check=False)


# ---------------------------------------------------------------------------
# REST routes (infrastructure)
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    ifaces = list_usb_ifaces()
    namespaces = list_netns()
    ns_details = []
    for ns in namespaces:
        ns_ifaces = list_ns_ifaces(ns)
        port = webui_port_for_namespace(ns)
        ns_details.append({"name": ns, "interfaces": ns_ifaces, "webui_port": port})
    return jsonify({"status": "success", "data": {"host_interfaces": ifaces, "namespaces": ns_details}})


@app.route("/api/isolate", methods=["POST"])
def api_isolate():
    data = request.json or {}
    iface = data.get("interface", "").strip()
    if not iface:
        return jsonify({"status": "error", "message": "Missing 'interface'."}), 400

    is_wwan = iface.startswith("wwan")
    ns = next_namespace_name()
    logs: List[str] = []

    try:
        # For wwan interfaces: capture IP config BEFORE moving (it's lost on netns transfer)
        saved_ip = None
        saved_gw = None
        if is_wwan:
            saved_ip = _get_iface_ip4(iface)
            saved_gw = _get_default_gw(iface)
            if saved_ip:
                logs.append(f"Saved IP config: {saved_ip} (gw: {saved_gw or 'none'})")
            else:
                logs.append("WARNING: wwan interface has no IPv4 address — isolating anyway.")

        _run(["ip", "netns", "add", ns], check=False)
        logs.append(f"Namespace '{ns}' created.")

        _run(["ip", "link", "set", iface, "netns", ns])
        logs.append(f"Interface '{iface}' → '{ns}'.")

        _run(["ip", "netns", "exec", ns, "ip", "link", "set", "lo", "up"])
        _run(["ip", "netns", "exec", ns, "ip", "link", "set", iface, "up"])
        logs.append("Loopback + interface UP.")

        if is_wwan:
            # WWAN: IP is assigned by modem firmware (MBIM/QMI), dhclient doesn't apply.
            # Re-apply the saved address and routing.
            if saved_ip:
                _run(["ip", "netns", "exec", ns, "ip", "addr", "add", saved_ip, "dev", iface], check=False)
                logs.append(f"IP address restored: {saved_ip}")
            if saved_gw:
                _run(["ip", "netns", "exec", ns, "ip", "route", "add", "default",
                      "via", saved_gw, "dev", iface], check=False)
                logs.append(f"Default route via gateway {saved_gw}.")
            else:
                # Point-to-point style: route without explicit gateway
                _run(["ip", "netns", "exec", ns, "ip", "route", "add", "default",
                      "dev", iface], check=False)
                logs.append(f"Default route via device {iface} (no gateway).")
        else:
            # USB Ethernet dongle: acquire IP via DHCP
            if shutil.which("dhclient"):
                _run(["ip", "netns", "exec", ns, "dhclient", "-r", iface], check=False)
                _run(["ip", "netns", "exec", ns, "dhclient", "-v", iface], check=False)
                logs.append("DHCP lease acquired.")
            else:
                logs.append("WARNING: dhclient not found.")
            _run(["ip", "netns", "exec", ns, "ip", "route", "add", "default", "dev", iface], check=False)
            logs.append(f"Default route via '{iface}'.")

        port = start_webui_tunnel(ns) if not is_wwan else None
        if port:
            logs.append(f"WebUI tunnel: http://localhost:{port}")

        return jsonify({"status": "success", "message": f"'{iface}' isolated → '{ns}'.",
                        "data": {"namespace": ns, "interface": iface, "webui_port": port,
                                 "logs": logs, "iface_type": "wwan" if is_wwan else "usb",
                                 "restored_ip": saved_ip}})
    except subprocess.CalledProcessError as exc:
        logs.append(f"FAILED: {exc}")
        return jsonify({"status": "error", "message": str(exc), "data": {"logs": logs}}), 500


@app.route("/api/reset", methods=["POST"])
def api_reset():
    data = request.json or {}
    ns = data.get("namespace", "").strip()
    if not ns:
        return jsonify({"status": "error", "message": "Missing 'namespace'."}), 400

    logs: List[str] = []
    try:
        stop_webui_tunnel(ns)
        logs.append("Socat tunnel stopped.")

        for iface in list_ns_ifaces(ns):
            _run(["ip", "-n", ns, "link", "set", iface, "netns", "1"], check=False)
            _run(["ip", "link", "set", iface, "up"], check=False)
            logs.append(f"'{iface}' → host.")

        _run(["ip", "netns", "delete", ns], check=False)
        logs.append(f"Namespace '{ns}' deleted.")
        _run(["pkill", "-f", "dhclient"], check=False)
        logs.append("Cleanup done.")

        return jsonify({"status": "success", "message": f"'{ns}' reset.", "data": {"logs": logs}})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc), "data": {"logs": logs}}), 500


@app.route("/api/benchmark", methods=["POST"])
def api_benchmark():
    """Quick (blocking) benchmark for the metric cards."""
    data = request.json or {}
    ns = data.get("namespace", "ue1")
    target_ip = data.get("target_ip", "10.45.0.1")
    results: Dict[str, Any] = {"timestamp": int(time.time()), "target": target_ip}

    try:
        ping_out = _run_shell(f"ip netns exec {ns} ping -c 5 -q {target_ip}")
        try:
            avg_ping = float(ping_out.split("=")[1].split("/")[1])
            results["ping_idle_ms"] = round(avg_ping, 2)
        except Exception:
            results["ping_idle_ms"] = -1

        dl_out = _run_shell(f"ip netns exec {ns} iperf3 -c {target_ip} -R -t 5 -J")
        dl_data = json.loads(dl_out)
        results["dl_mbps"] = round(dl_data.get("end", {}).get("sum_received", {}).get("bits_per_second", 0) / 1e6, 2)

        ul_out = _run_shell(f"ip netns exec {ns} iperf3 -c {target_ip} -t 5 -J")
        ul_data = json.loads(ul_out)
        results["ul_mbps"] = round(ul_data.get("end", {}).get("sum_sent", {}).get("bits_per_second", 0) / 1e6, 2)

        results["status"] = "success"
    except Exception as exc:
        results["status"] = "error"
        results["message"] = str(exc)
    return jsonify(results)


# ---------------------------------------------------------------------------
# Module-level iperf3 phase runner
# ---------------------------------------------------------------------------

def run_iperf_phase(session: BenchmarkSession, phase_mode: str, phase_duration: int) -> dict:
    """Run one iperf3 phase, emit iperf_data events, return full phase summary dict.

    The summary includes:
      - client_intervals: list of per-interval dicts (client_mbps + server_mbps backfilled
        from server_intervals when available)
      - server_intervals: list of per-interval dicts from --get-server-output block
      - client_mbps, server_mbps, loss_pct, jitter_ms, total_retr: final summary values
    """
    reverse_flag = ["-R"] if phase_mode == "dl" else []
    udp_flags = ["-u", "-b", session.bandwidth] if session.proto == "udp" else []
    # UDP: -l sets datagram payload size; TCP: no length flag (use default MSS from path MTU)
    length_flag = ["-l", str(session.length_bytes)] if session.proto == "udp" else []

    # -i 0.1 → 10 samples/s; --get-server-output → actual receiver telemetry
    iperf_cmd = [
        "ip", "netns", "exec", session.ns,
        "iperf3", "-c", session.target_ip,
        "-t", str(phase_duration), "-i", "0.1",
        "-f", "m", "--forceflush", "--get-server-output",
    ] + udp_flags + length_flag + reverse_flag

    # UDP UL: client is the sender → interval stats are injected traffic, NOT received
    is_sender_side = session.proto == "udp" and phase_mode != "dl"

    proto_label = f"UDP ({session.bandwidth})" if session.proto == "udp" else "TCP"
    socketio.emit("test_status", {
        "status": "running",
        "message": f"Running {phase_mode.upper()} [{proto_label}] for {phase_duration}s…",
    }, to=session.sid)

    iperf_proc = subprocess.Popen(
        iperf_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    with _active_lock:
        if session.sid not in _active_procs:
            _active_procs[session.sid] = []
        _active_procs[session.sid].append(iperf_proc)

    phase_summary: Dict[str, Any] = {
        "phase": phase_mode,
        "proto": session.proto,
        "sender_side": is_sender_side,
        "server_intervals": [],
        "client_intervals": [],
        "raw_lines": [],
    }
    total_retr = 0
    in_server_output = False

    for line in iter(iperf_proc.stdout.readline, ""):
        if iperf_proc.poll() is not None and not line:
            break
        phase_summary["raw_lines"].append(line)
        line = line.strip()
        if not line:
            continue

        if "Server output:" in line:
            in_server_output = True
            continue

        # --- Final summary lines (contain "sender" or "receiver") ---
        if "sender" in line or "receiver" in line:
            sm = re.search(r"([\d.]+)\s*Mbits/sec", line)
            if sm:
                mbps_val = float(sm.group(1))
                if "receiver" in line:
                    phase_summary["receiver_mbps"] = mbps_val
                    lm = re.search(r"(\d+)/(\d+)\s*\(([\d.]+)%\)", line)
                    if lm:
                        phase_summary["lost"] = int(lm.group(1))
                        phase_summary["total"] = int(lm.group(2))
                        phase_summary["loss_pct"] = float(lm.group(3))
                    jm = re.search(r"([\d.]+)\s*ms", line)
                    if jm:
                        phase_summary["jitter_ms"] = float(jm.group(1))
                else:
                    if not in_server_output:
                        phase_summary["sender_mbps"] = mbps_val
                        rm = re.search(r"Mbits/sec\s+(\d+)\s", line)
                        if rm:
                            phase_summary["total_retr"] = int(rm.group(1))
            continue  # never chart summary lines

        # --- Interval lines ---
        m = re.search(r"([\d.]+)\s*Mbits/sec", line)
        if not m:
            continue
        mbps = float(m.group(1))

        if in_server_output:
            # Parse the server-side interval: capture time bounds + UDP stats
            tm = re.search(r"([\d.]+)-([\d.]+)\s+sec", line)
            if not tm:
                continue
            i_start, i_end = float(tm.group(1)), float(tm.group(2))
            if i_start == i_end:
                continue  # skip zero-duration artifacts
            srv: Dict[str, Any] = {
                "mbps": mbps,
                "interval_start": i_start,
                "interval_end": i_end,
            }
            if session.proto == "udp":
                # Token-based: find "ms" token → preceding token is jitter value.
                # More robust than regex against variable whitespace/iperf3 versions.
                tokens = line.split()
                for j, tok in enumerate(tokens):
                    if tok == "ms" and j > 0:
                        try:
                            srv["jitter_ms"] = float(tokens[j - 1])
                        except ValueError:
                            pass
                        break
                # Loss: X/Y (P%) pattern
                lm = re.search(r"(\d+)/(\d+)\s*\(([\d.]+)%\)", line)
                if lm:
                    srv["loss_pct"] = float(lm.group(3))
                    srv["lost"] = int(lm.group(1))
                    srv["total"] = int(lm.group(2))
            phase_summary["server_intervals"].append(srv)

        else:
            # Client-side interval — emit live, accumulate for CSV
            session.second_counter["val"] = round(session.second_counter["val"] + 0.1, 2)
            t_val = session.second_counter["val"]

            evt: Dict[str, Any] = {
                "mbps": mbps,
                "second": t_val,
                "proto": session.proto,
                "phase": phase_mode,
                "sender_side": is_sender_side,
                "raw": line,
            }

            # UDP receiver-side interval (UDP DL): jitter + loss are accurate
            if session.proto == "udp" and not is_sender_side:
                tokens = line.split()
                for j, tok in enumerate(tokens):
                    if tok == "ms" and j > 0:
                        try:
                            evt["jitter_ms"] = float(tokens[j - 1])
                        except ValueError:
                            pass
                        break
                lm = re.search(r"(\d+)/(\d+)\s*\(([\d.]+)%\)", line)
                if lm:
                    evt["loss_pct"] = float(lm.group(3))
                    evt["lost"] = int(lm.group(1))
                    evt["total"] = int(lm.group(2))

            # TCP sender-side interval: retransmits + cwnd (token after Mbits/sec)
            if session.proto == "tcp":
                tokens = line.split()
                try:
                    mbps_idx = next(i for i, t in enumerate(tokens) if t == "Mbits/sec")
                    # Format: … Mbits/sec  <retr>  <cwnd_val> <KBytes|MBytes>
                    if mbps_idx + 3 < len(tokens):
                        retr = int(tokens[mbps_idx + 1])
                        cwnd_val = float(tokens[mbps_idx + 2])
                        unit = tokens[mbps_idx + 3]
                        cwnd_kb = cwnd_val * 1024 if unit == "MBytes" else cwnd_val
                        evt["retr"] = retr
                        evt["cwnd_kb"] = cwnd_kb
                        total_retr += retr
                except (StopIteration, ValueError, IndexError):
                    pass

            socketio.emit("iperf_data", evt, to=session.sid)

            # Accumulate for CSV export
            phase_summary["client_intervals"].append({
                "protocol":     session.proto,
                "phase":        phase_mode,
                "timestamp_s":  t_val,
                "interval_start": round(t_val - 0.1, 2),
                "interval_end": t_val,
                "length_bytes": session.length_bytes if session.proto == "udp" else None,
                "client_mbps":  mbps,
                "server_mbps":  None,       # filled below from server_intervals
                "loss_pct":     evt.get("loss_pct"),
                "jitter_ms":    evt.get("jitter_ms"),
                "retransmits":  evt.get("retr"),
                "cwnd_kb":      evt.get("cwnd_kb"),
            })

    iperf_proc.wait()
    with _active_lock:
        if session.sid in _active_procs and iperf_proc in _active_procs[session.sid]:
            _active_procs[session.sid].remove(iperf_proc)

    # Remap iperf3's "sender"/"receiver" summary labels → client/server
    # For DL (-R): iperf3 "receiver" = client (who received), "sender" = server (who sent)
    # For UL:      iperf3 "sender" = client (who sent),     "receiver" = server (who received)
    if phase_mode == "dl":
        phase_summary["client_mbps"] = phase_summary.pop("receiver_mbps", None)
        phase_summary["server_mbps"] = phase_summary.pop("sender_mbps", None)
    else:
        phase_summary["client_mbps"] = phase_summary.pop("sender_mbps", None)
        phase_summary["server_mbps"] = phase_summary.pop("receiver_mbps", None)

    # Back-fill server telemetry (mbps, loss, jitter) from server_intervals
    n = min(len(phase_summary["client_intervals"]), len(phase_summary["server_intervals"]))
    for i in range(n):
        srv = phase_summary["server_intervals"][i]
        ci = phase_summary["client_intervals"][i]
        ci["server_mbps"] = srv["mbps"]
        if "loss_pct" in srv:
            ci["loss_pct"] = srv["loss_pct"]
        if "jitter_ms" in srv:
            ci["jitter_ms"] = srv["jitter_ms"]

    # Emit backfilled data so the frontend can update its tpData for CSV export
    if phase_summary["server_intervals"]:
        socketio.emit("iperf_backfill", {
            "phase": phase_mode,
            "intervals": [
                {"loss_pct": ci.get("loss_pct"), "jitter_ms": ci.get("jitter_ms")}
                for ci in phase_summary["client_intervals"]
            ],
        }, to=session.sid)

    return phase_summary


# ---------------------------------------------------------------------------
# SocketIO — Live Benchmark
# ---------------------------------------------------------------------------

def _live_benchmark_thread(sid: str, ns: str, target_ip: str, duration: int, mode: str,
                           proto: str = "tcp", bandwidth: str = "200M",
                           length_bytes: int = 1200, with_ping: bool = True):
    """Background thread: runs iperf3 (optionally + ping) and streams data via SocketIO."""

    ping_cmd = ["ip", "netns", "exec", ns, "ping", target_ip]
    tests_done = threading.Event()
    ping_proc = None
    t_ping = None
    session = BenchmarkSession(
        sid=sid, ns=ns, target_ip=target_ip, proto=proto,
        bandwidth=bandwidth, length_bytes=length_bytes,
    )

    try:
        if with_ping:
            ping_proc = subprocess.Popen(
                ping_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            with _active_lock:
                if sid not in _active_procs:
                    _active_procs[sid] = []
                _active_procs[sid].append(ping_proc)

            def read_ping():
                seq = 0
                while not tests_done.is_set() or (ping_proc and ping_proc.poll() is None):
                    line = ping_proc.stdout.readline() if ping_proc else ""
                    if not line:
                        if tests_done.is_set():
                            break
                        continue
                    line = line.strip()
                    m = re.search(r"time[=<]([\d.]+)", line)
                    if m:
                        ms = float(m.group(1))
                        seq += 1
                        socketio.emit("ping_data", {"ms": ms, "seq": seq, "raw": line}, to=sid)

            t_ping = threading.Thread(target=read_ping, daemon=True)
            t_ping.start()

        socketio.emit("test_status", {"status": "starting", "message": "Launching benchmark..."}, to=sid)

        phase_results = []
        if mode == "both":
            half_dur = max(duration // 2, 3)
            phase_results.append(run_iperf_phase(session, "dl", half_dur))
            time.sleep(1)
            phase_results.append(run_iperf_phase(session, "ul", half_dur))
        else:
            phase_results.append(run_iperf_phase(session, mode, duration))

        tests_done.set()

        if ping_proc:
            ping_proc.terminate()
            try:
                ping_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                ping_proc.kill()
        if t_ping:
            t_ping.join(timeout=3)

        socketio.emit("test_complete", {
            "status": "done", "message": "Benchmark complete.",
            "phases": phase_results,
        }, to=sid)

    except Exception as exc:
        socketio.emit("test_complete", {"status": "error", "message": str(exc)}, to=sid)

    finally:
        tests_done.set()
        with _active_lock:
            procs = _active_procs.pop(sid, [])
            for p in procs:
                if p and p.poll() is None:
                    p.kill()


@socketio.on("start_live_benchmark")
def handle_start_live_benchmark(data):
    """Client requests a live benchmark. Spawns a background thread."""
    sid = request.sid

    if _plan_running.is_set():
        emit("test_complete", {"status": "error",
                               "message": "A plan is currently running. Abort the plan first."})
        return

    ns = data.get("namespace", "ue1")
    target_ip = data.get("target_ip", "10.45.0.1")
    duration = min(int(data.get("duration", 10)), 120)
    mode = data.get("mode", "dl")
    proto = data.get("proto", "tcp")
    bandwidth = data.get("bandwidth", "200M")
    length_bytes = max(68, min(int(data.get("length_bytes", 1200)), 65507))
    with_ping = bool(data.get("with_ping", False))

    with _active_lock:
        for p in _active_procs.get(sid, []):
            if p and p.poll() is None:
                p.kill()
        _active_procs.pop(sid, None)

    t = threading.Thread(
        target=_live_benchmark_thread,
        args=(sid, ns, target_ip, duration, mode, proto, bandwidth, length_bytes, with_ping),
        daemon=True,
    )
    t.start()


@socketio.on("stop_live_test")
def handle_stop_live_test(data=None):
    """Stop any running live test (iperf or ping) for this client."""
    sid = request.sid
    with _active_lock:
        procs = _active_procs.pop(sid, [])
    for p in procs:
        if p and p.poll() is None:
            p.kill()
    emit("test_complete", {"status": "stopped", "message": "Test stopped by user."})


# Keep old name for compatibility
@socketio.on("stop_live_benchmark")
def handle_stop_live_benchmark(data=None):
    handle_stop_live_test(data)


# ---------------------------------------------------------------------------
# SocketIO — Live Ping Test
# ---------------------------------------------------------------------------

def _live_ping_thread(sid: str, ns: str, target_ip: str, count: int,
                      interval: float, packet_size: int = 56):
    """Background thread: runs ping, parses per-packet RTT, emits ping_data."""
    ping_cmd = [
        "ip", "netns", "exec", ns,
        "ping", "-c", str(count), "-i", str(interval), "-s", str(packet_size),
        target_ip,
    ]

    proc = None
    try:
        socketio.emit("test_status", {
            "status": "starting",
            "message": f"Pinging {target_ip} ({count} packets, size={packet_size}B, interval={interval}s)…",
        }, to=sid)

        proc = subprocess.Popen(
            ping_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        with _active_lock:
            _active_procs[sid] = [proc]

        socketio.emit("test_status", {"status": "running", "message": "Live ping running…"}, to=sid)

        seq = 0
        for line in iter(proc.stdout.readline, ""):
            if proc.poll() is not None and not line:
                break
            line = line.strip()
            m_rtt = re.search(r"time[=<]([\d.]+)\s*ms", line)
            m_seq = re.search(r"icmp_seq=(\d+)", line)
            m_ttl = re.search(r"ttl=(\d+)", line)
            m_bytes = re.search(r"^(\d+)\s+bytes", line)
            if m_rtt:
                seq = int(m_seq.group(1)) if m_seq else seq + 1
                payload = {
                    "seq": seq,
                    "ms": float(m_rtt.group(1)),
                    "ttl": int(m_ttl.group(1)) if m_ttl else None,
                    "bytes": int(m_bytes.group(1)) if m_bytes else None,
                }
                socketio.emit("ping_data", payload, to=sid)

        proc.wait()
        socketio.emit("test_complete", {"status": "done", "message": "Ping test complete."}, to=sid)

    except Exception as exc:
        socketio.emit("test_complete", {"status": "error", "message": str(exc)}, to=sid)
    finally:
        if proc and proc.poll() is None:
            proc.kill()
        with _active_lock:
            _active_procs.pop(sid, None)


@socketio.on("start_live_ping")
def handle_start_live_ping(data):
    """Client requests a dedicated live ping test."""
    sid = request.sid

    if _plan_running.is_set():
        emit("test_complete", {"status": "error",
                               "message": "A plan is currently running. Abort the plan first."})
        return

    ns = data.get("namespace", "ue1")
    target_ip = data.get("target_ip", "10.45.0.1")
    count = min(int(data.get("count", 60)), 3000)
    interval = max(float(data.get("interval", 0.5)), 0.1)          # min 0.1s
    packet_size = max(8, min(int(data.get("packet_size", 56)), 65507))

    with _active_lock:
        for p in _active_procs.get(sid, []):
            if p and p.poll() is None:
                p.kill()
        _active_procs.pop(sid, None)

    t = threading.Thread(
        target=_live_ping_thread,
        args=(sid, ns, target_ip, count, interval, packet_size),
        daemon=True,
    )
    t.start()


@socketio.on("connect")
def handle_connect():
    emit("test_status", {"status": "connected", "message": "Connected to 5G Probe server."})


@socketio.on("disconnect")
def handle_disconnect():
    sid = request.sid
    with _active_lock:
        procs = _active_procs.pop(sid, [])
    for p in procs:
        if p and p.poll() is None:
            p.kill()


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

_IPERF_CSV_FIELDS = [
    "#", "protocol", "phase", "time_s",
    "interval_start_s", "interval_end_s", "pkt_bytes",
    "client_Mbps", "server_Mbps",
    "loss_pct", "jitter_ms",
    "retransmits", "cwnd_kB",
]

_PING_CSV_FIELDS = [
    "#", "time_s", "rtt_ms", "owd_ms", "jitter_ms", "ttl", "pkt_bytes",
]

def _normalise_iperf_row(row: dict, idx: int) -> dict:
    """Map internal interval dict keys → CSV column names."""
    return {
        "#":               idx,
        "protocol":        row.get("protocol", ""),
        "phase":           row.get("phase", ""),
        "time_s":          row.get("timestamp_s", row.get("interval_end", "")),
        "interval_start_s": row.get("interval_start", ""),
        "interval_end_s":  row.get("interval_end", ""),
        "pkt_bytes":       row.get("length_bytes", ""),
        "client_Mbps":     row.get("client_mbps", ""),
        "server_Mbps":     row.get("server_mbps", ""),
        "loss_pct":        row.get("loss_pct", ""),
        "jitter_ms":       row.get("jitter_ms", ""),
        "retransmits":     row.get("retransmits", ""),
        "cwnd_kB":         row.get("cwnd_kb", ""),
    }

def _write_iperf_csv(path: str, phase_results: List[dict]) -> None:
    """Write client_intervals from all phases to a CSV file."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_IPERF_CSV_FIELDS)
        w.writeheader()
        idx = 1
        for ph in phase_results:
            for row in ph.get("client_intervals", []):
                w.writerow(_normalise_iperf_row(row, idx))
                idx += 1


def _write_ping_csv(path: str, ping_rows: List[dict]) -> None:
    """Write per-packet ping data to a CSV file."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_PING_CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for i, row in enumerate(ping_rows, 1):
            out = {
                "#":       i,
                "time_s":  row.get("timestamp_s", row.get("time_s", "")),
                "rtt_ms":  row.get("rtt_ms", ""),
                "owd_ms":  row.get("owd_ms", ""),
                "jitter_ms": row.get("jitter_ms", ""),
                "ttl":     row.get("ttl", ""),
                "pkt_bytes": row.get("packet_size", row.get("pkt_bytes", "")),
            }
            w.writerow(out)


def _write_raw_iperf_output(filepath: str, phase_results: List[dict]) -> None:
    """Write raw iperf3 stdout from all phases to a text file."""
    with open(filepath, "w") as f:
        for i, ph in enumerate(phase_results):
            if i > 0:
                f.write(f"\n{'='*60}\n")
                f.write(f"Phase: {ph.get('phase', '').upper()}\n")
                f.write(f"{'='*60}\n\n")
            for raw_line in ph.get("raw_lines", []):
                f.write(raw_line)


# ---------------------------------------------------------------------------
# Test Results — save & browse
# ---------------------------------------------------------------------------

def _make_result_dir(result_path_hint: Optional[str],
                     test_type: str, direction: str, protocol: str) -> str:
    """Create and return the result subdirectory path."""
    if result_path_hint and result_path_hint.startswith("plans/"):
        result_dir = os.path.join(RESULTS_DIR, result_path_hint)
    else:
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H-%M-%S")
        parts = [time_str, test_type]
        if direction:
            parts.append(direction)
        if protocol:
            parts.append(protocol)
        result_dir = os.path.join(RESULTS_DIR, "standalone", date_str, "_".join(parts))
    os.makedirs(result_dir, exist_ok=True)
    return result_dir


@app.route("/api/save_result", methods=["POST"])
def save_result():
    """Save a test result: chart PNG, CSV data, and structured metadata JSON."""
    try:
        data = request.json
        image_b64 = data.get("image", "")
        test_type = data.get("test_type", "unknown")
        direction = data.get("direction", "")
        protocol = data.get("protocol", "")
        bandwidth = data.get("bandwidth", "")
        namespace = data.get("namespace", "")
        target_ip = data.get("target_ip", "")
        duration_s = data.get("duration_s")
        summary_stats = data.get("summary_stats", {})
        intervals = data.get("intervals", [])    # flat list of iperf interval row dicts
        ping_rows = data.get("ping_rows", [])    # flat list of ping row dicts
        result_path_hint = data.get("result_path_hint")

        result_dir = _make_result_dir(result_path_hint, test_type, direction, protocol)
        now = datetime.now()

        # Save chart.png
        if image_b64:
            img_data = image_b64.split(",", 1)[1] if "," in image_b64 else image_b64
            with open(os.path.join(result_dir, "chart.png"), "wb") as f:
                f.write(base64.b64decode(img_data))

        # Save data.csv
        csv_written = False
        if intervals:
            with open(os.path.join(result_dir, "data.csv"), "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=_IPERF_CSV_FIELDS)
                w.writeheader()
                for idx, row in enumerate(intervals, 1):
                    w.writerow(_normalise_iperf_row(row, idx))
            csv_written = True
        elif ping_rows:
            _write_ping_csv(os.path.join(result_dir, "data.csv"), ping_rows)
            csv_written = True

        # Save metadata.json
        meta = {
            "test_type": test_type,
            "direction": direction,
            "protocol": protocol,
            "bandwidth": bandwidth,
            "namespace": namespace,
            "target_ip": target_ip,
            "duration_s": duration_s,
            "timestamp": now.isoformat(),
            "chart": "chart.png" if image_b64 else None,
            "csv": "data.csv" if csv_written else None,
            "summary": summary_stats,
        }
        with open(os.path.join(result_dir, "metadata.json"), "w") as f:
            json.dump(meta, f, indent=2)

        rel_path = os.path.relpath(result_dir, RESULTS_DIR)
        return jsonify({"ok": True, "result_path": rel_path})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/results")
def list_results():
    """List all saved results: standalone (new-style + legacy) and plans."""
    import collections

    result: Dict[str, Any] = {
        "standalone": collections.OrderedDict(),
        "legacy": collections.OrderedDict(),
        "plans": {},
    }

    # --- New-style standalone: results/standalone/{date}/{folder}/metadata.json ---
    standalone_dir = os.path.join(RESULTS_DIR, "standalone")
    if os.path.isdir(standalone_dir):
        for date_folder in sorted(os.listdir(standalone_dir), reverse=True):
            date_path = os.path.join(standalone_dir, date_folder)
            if not os.path.isdir(date_path):
                continue
            items = []
            for entry in sorted(os.listdir(date_path), reverse=True):
                entry_path = os.path.join(date_path, entry)
                if not os.path.isdir(entry_path):
                    continue
                meta_path = os.path.join(entry_path, "metadata.json")
                if not os.path.exists(meta_path):
                    continue
                try:
                    with open(meta_path) as f:
                        meta = json.load(f)
                    meta["result_path"] = f"standalone/{date_folder}/{entry}"
                    items.append(meta)
                except Exception:
                    pass
            if items:
                result["standalone"][date_folder] = items

    # --- Legacy flat structure: results/{YYYY-MM-DD}/*.json ---
    for entry in sorted(os.listdir(RESULTS_DIR), reverse=True):
        entry_path = os.path.join(RESULTS_DIR, entry)
        if not os.path.isdir(entry_path):
            continue
        if entry in ("standalone", "plans"):
            continue
        items = []
        for fname in sorted(os.listdir(entry_path), reverse=True):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(entry_path, fname)) as f:
                        meta = json.load(f)
                    meta["date"] = entry
                    meta["_legacy"] = True
                    items.append(meta)
                except Exception:
                    pass
        if items:
            result["legacy"][entry] = items

    # --- Plans: results/plans/{name}/plan.json ---
    if os.path.isdir(PLANS_DIR):
        for plan_name in sorted(os.listdir(PLANS_DIR)):
            plan_path = os.path.join(PLANS_DIR, plan_name)
            if not os.path.isdir(plan_path):
                continue
            plan_json = os.path.join(plan_path, "plan.json")
            if not os.path.exists(plan_json):
                continue
            try:
                with open(plan_json) as f:
                    plan_data = json.load(f)
                exps = plan_data.get("experiments", [])
                result["plans"][plan_name] = {
                    "plan": plan_data,
                    "total": len(exps),
                    "completed": sum(1 for e in exps if e.get("state") == "completed"),
                    "failed": sum(1 for e in exps if e.get("state") == "failed"),
                    "pending": sum(1 for e in exps if e.get("state") in ("pending", "running")),
                }
            except Exception:
                pass

    return jsonify(result)


@app.route("/api/results/<path:subpath>", methods=["GET"])
def serve_result(subpath):
    """Serve any file under RESULTS_DIR (handles both legacy and new paths)."""
    return send_from_directory(RESULTS_DIR, subpath)


@app.route("/api/results/<path:subpath>", methods=["DELETE"])
def delete_result(subpath):
    """Delete a result. For new-style folders: removes the directory. For legacy: PNG+JSON pair."""
    try:
        full_path = os.path.join(RESULTS_DIR, subpath)
        if os.path.isdir(full_path):
            shutil.rmtree(full_path)
        elif os.path.isfile(full_path):
            os.remove(full_path)
            # Remove companion .json if this was a legacy .png
            if full_path.endswith(".png"):
                json_path = full_path.replace(".png", ".json")
                if os.path.exists(json_path):
                    os.remove(json_path)
            # Clean up empty parent directory
            parent = os.path.dirname(full_path)
            if os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/open_folder/<path:subpath>", methods=["POST"])
def open_result_folder(subpath):
    """Open a result folder in the host file manager via xdg-open."""
    try:
        folder_path = os.path.join(RESULTS_DIR, subpath)
        if not os.path.isdir(folder_path):
            # Legacy: subpath may be a file — open its parent dir
            parent = os.path.dirname(folder_path)
            if os.path.isdir(parent):
                folder_path = parent
            else:
                return jsonify({"ok": False, "error": "Folder not found"}), 404

        env = os.environ.copy()
        # xdg-open needs DISPLAY and DBUS to reach the desktop session
        env.setdefault("DISPLAY", ":0")
        uid = os.getuid()
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")

        # Fire-and-forget: do NOT wait — xdg-open may block for seconds
        subprocess.Popen(
            ["xdg-open", folder_path],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return jsonify({"ok": True, "path": folder_path})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "xdg-open not found"}), 500
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Plan management helpers
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    slug = re.sub(r"[^\w\-]", "_", name.lower().strip())
    slug = re.sub(r"_+", "_", slug).strip("_")
    if not slug or "/" in slug or ".." in slug:
        raise ValueError(f"Invalid plan name: '{name}'")
    return slug


def _load_plan(plan_name: str) -> dict:
    plan_path = os.path.join(PLANS_DIR, plan_name, "plan.json")
    with open(plan_path) as f:
        return json.load(f)


def _save_plan(plan_name: str, plan: dict) -> None:
    plan["updated_at"] = datetime.now().isoformat()
    plan_path = os.path.join(PLANS_DIR, plan_name, "plan.json")
    with open(plan_path, "w") as f:
        json.dump(plan, f, indent=2)


def _save_run(plan_name: str, run_id: str, run_data: dict) -> None:
    """Write run.json into the run directory."""
    run_dir = os.path.join(PLANS_DIR, plan_name, f"run_{run_id}")
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "run.json"), "w") as f:
        json.dump(run_data, f, indent=2)


def _list_runs(plan_name: str) -> List[dict]:
    """List all runs for a plan, sorted by date descending."""
    plan_dir = os.path.join(PLANS_DIR, plan_name)
    if not os.path.isdir(plan_dir):
        return []
    runs = []
    for entry in os.listdir(plan_dir):
        if not entry.startswith("run_") or not os.path.isdir(os.path.join(plan_dir, entry)):
            continue
        run_id = entry[4:]  # strip "run_"
        run_json = os.path.join(plan_dir, entry, "run.json")
        if os.path.exists(run_json):
            try:
                with open(run_json) as f:
                    rd = json.load(f)
                runs.append(rd)
            except Exception:
                pass
        else:
            # Backward compat: reconstruct from plan.json if run.json missing
            try:
                plan = _load_plan(plan_name)
                if plan.get("run_id") == run_id:
                    exps = plan.get("experiments", [])
                    completed = sum(1 for e in exps if e.get("state") == "completed")
                    failed = sum(1 for e in exps if e.get("state") == "failed")
                    total = len(exps)
                    status = "completed" if completed == total else (
                        "partial" if completed > 0 else "pending"
                    )
                    runs.append({
                        "run_id": run_id,
                        "plan_name": plan_name,
                        "started_at": min(
                            (e["started_at"] for e in exps if e.get("started_at")),
                            default=None,
                        ),
                        "completed_at": max(
                            (e["completed_at"] for e in exps if e.get("completed_at")),
                            default=None,
                        ),
                        "status": status,
                        "total": total,
                        "completed": completed,
                        "failed": failed,
                        "experiments": exps,
                    })
            except Exception:
                pass
    runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return runs


def _experiments_summary(experiments: List[dict]) -> str:
    """Build a human-readable summary like '2x Throughput TCP, 2x Throughput UDP'."""
    from collections import Counter
    counts: Counter = Counter()
    for e in experiments:
        t = (e.get("type", "").title() or "Test")
        p = (e.get("protocol", "").upper() or "")
        key = f"{t} {p}".strip()
        counts[key] += 1
    parts = []
    for key, cnt in counts.most_common():
        parts.append(f"{cnt}x {key}" if cnt > 1 else key)
    return ", ".join(parts) if parts else "No experiments"


def _expand_experiments(raw_experiments: List[dict]) -> List[dict]:
    """Expand bandwidth range specs into individual experiment entries."""
    result = []
    idx = 1
    for exp_tmpl in raw_experiments:
        tmpl = copy.deepcopy(exp_tmpl)
        bw_start = tmpl.pop("bandwidth_start", None)
        bw_end = tmpl.pop("bandwidth_end", None)
        bw_step = tmpl.pop("bandwidth_step", None)

        if bw_start is not None and bw_end is not None and bw_step:
            bw = int(bw_start)
            while bw <= int(bw_end):
                e = copy.deepcopy(tmpl)
                e["bandwidth"] = f"{bw}M"
                e["id"] = f"exp_{idx:03d}"
                e["label"] = (
                    f"{e.get('type','').title()} "
                    f"{e.get('direction','').upper()} "
                    f"{e.get('protocol','').upper()} {bw}M"
                ).strip()
                e.setdefault("state", "pending")
                e.setdefault("result_path", None)
                e.setdefault("error", None)
                e.setdefault("started_at", None)
                e.setdefault("completed_at", None)
                result.append(e)
                bw += int(bw_step)
                idx += 1
        else:
            e = copy.deepcopy(tmpl)
            e["id"] = f"exp_{idx:03d}"
            if not e.get("label"):
                parts = [
                    e.get("type", ""), e.get("direction", ""),
                    e.get("protocol", ""), e.get("bandwidth", ""),
                ]
                e["label"] = " ".join(p for p in parts if p).title()
            e.setdefault("state", "pending")
            e.setdefault("result_path", None)
            e.setdefault("error", None)
            e.setdefault("started_at", None)
            e.setdefault("completed_at", None)
            result.append(e)
            idx += 1
    return result


# ---------------------------------------------------------------------------
# Plan REST routes
# ---------------------------------------------------------------------------

@app.route("/api/plans", methods=["GET"])
def list_plans():
    plans = []
    if not os.path.isdir(PLANS_DIR):
        return jsonify({"plans": []})
    for plan_name in sorted(os.listdir(PLANS_DIR)):
        plan_dir = os.path.join(PLANS_DIR, plan_name)
        if not os.path.isdir(plan_dir):
            continue
        plan_json = os.path.join(plan_dir, "plan.json")
        if not os.path.exists(plan_json):
            continue
        try:
            with open(plan_json) as f:
                plan = json.load(f)
            exps = plan.get("experiments", [])
            plans.append({
                "name": plan_name,
                "created_at": plan.get("created_at"),
                "experiment_count": len(exps),
                "experiments_summary": _experiments_summary(exps),
                "experiments": [
                    {k: e.get(k) for k in ("id", "label", "type", "direction",
                     "protocol", "bandwidth", "duration", "ping_count",
                     "ping_interval", "ping_packet_size", "length_bytes")}
                    for e in exps
                ],
            })
        except Exception:
            pass
    return jsonify({"plans": plans})


@app.route("/api/plans", methods=["POST"])
def create_plan():
    data = request.json or {}
    name_raw = data.get("name", "").strip()
    experiments_raw = data.get("experiments", [])
    config = data.get("config", {})

    if not name_raw:
        return jsonify({"ok": False, "error": "Plan name is required"}), 400

    try:
        plan_name = _slugify(name_raw)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    plan_dir = os.path.join(PLANS_DIR, plan_name)
    if os.path.exists(plan_dir):
        return jsonify({"ok": False, "error": f"Plan '{plan_name}' already exists"}), 409

    os.makedirs(plan_dir)
    try:
        experiments = _expand_experiments(experiments_raw)
    except Exception as e:
        shutil.rmtree(plan_dir, ignore_errors=True)
        return jsonify({"ok": False, "error": f"Failed to expand experiments: {e}"}), 400

    now = datetime.now().isoformat()
    plan = {
        "version": 1,
        "name": plan_name,
        "created_at": now,
        "updated_at": now,
        "config": config,
        "experiments": experiments,
    }
    with open(os.path.join(plan_dir, "plan.json"), "w") as f:
        json.dump(plan, f, indent=2)

    return jsonify({"ok": True, "plan_name": plan_name, "experiment_count": len(experiments)})


@app.route("/api/plans/<plan_name>", methods=["GET"])
def get_plan(plan_name):
    plan_path = os.path.join(PLANS_DIR, plan_name, "plan.json")
    if not os.path.exists(plan_path):
        return jsonify({"ok": False, "error": "Plan not found"}), 404
    with open(plan_path) as f:
        return jsonify(json.load(f))


@app.route("/api/plans/<plan_name>", methods=["DELETE"])
def delete_plan(plan_name):
    if _plan_running.is_set():
        return jsonify({"ok": False, "error": "A plan is currently executing. Abort it first."}), 409
    plan_dir = os.path.join(PLANS_DIR, plan_name)
    if not os.path.isdir(plan_dir):
        return jsonify({"ok": False, "error": "Plan not found"}), 404
    shutil.rmtree(plan_dir)
    return jsonify({"ok": True})


@app.route("/api/plans/<plan_name>/runs", methods=["GET"])
def list_plan_runs(plan_name):
    """List all runs for a plan, sorted by date descending."""
    plan_dir = os.path.join(PLANS_DIR, plan_name)
    if not os.path.isdir(plan_dir):
        return jsonify({"ok": False, "error": "Plan not found"}), 404
    runs = _list_runs(plan_name)
    # Return compact summaries
    summaries = []
    for r in runs:
        exps = r.get("experiments", [])
        summaries.append({
            "run_id": r.get("run_id"),
            "plan_name": plan_name,
            "started_at": r.get("started_at"),
            "completed_at": r.get("completed_at"),
            "status": r.get("status", "unknown"),
            "total": len(exps),
            "completed": sum(1 for e in exps if e.get("state") == "completed"),
            "failed": sum(1 for e in exps if e.get("state") == "failed"),
        })
    return jsonify({"runs": summaries})


@app.route("/api/plans/<plan_name>/runs/<run_id>", methods=["GET"])
def get_plan_run(plan_name, run_id):
    """Get full run detail with per-experiment results."""
    run_json = os.path.join(PLANS_DIR, plan_name, f"run_{run_id}", "run.json")
    if os.path.exists(run_json):
        with open(run_json) as f:
            return jsonify(json.load(f))
    # Backward compat: try reconstructing from plan.json
    runs = _list_runs(plan_name)
    for r in runs:
        if r.get("run_id") == run_id:
            return jsonify(r)
    return jsonify({"ok": False, "error": "Run not found"}), 404


@app.route("/api/run-history", methods=["GET"])
def run_history():
    """List all runs across all plans, sorted by date descending."""
    all_runs = []
    if not os.path.isdir(PLANS_DIR):
        return jsonify({"runs": []})
    for plan_name in os.listdir(PLANS_DIR):
        plan_dir = os.path.join(PLANS_DIR, plan_name)
        if not os.path.isdir(plan_dir):
            continue
        for r in _list_runs(plan_name):
            exps = r.get("experiments", [])
            all_runs.append({
                "run_id": r.get("run_id"),
                "plan_name": plan_name,
                "started_at": r.get("started_at"),
                "completed_at": r.get("completed_at"),
                "status": r.get("status", "unknown"),
                "total": len(exps),
                "completed": sum(1 for e in exps if e.get("state") == "completed"),
                "failed": sum(1 for e in exps if e.get("state") == "failed"),
            })
    all_runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return jsonify({"runs": all_runs})


@app.route("/api/queue", methods=["GET"])
def get_queue():
    """Get current run queue state."""
    with _queue_lock:
        return jsonify({
            "items": list(_run_queue),
            "delay_between_s": _queue_delay_s,
            "running": _queue_running.is_set(),
        })


@app.route("/api/queue", methods=["POST"])
def set_queue():
    """Set the run queue. Replaces the current queue."""
    global _queue_delay_s
    data = request.json or {}
    items = data.get("items", [])
    delay = int(data.get("delay_between_s", 5))
    # Validate plan names
    for item in items:
        pname = item.get("plan_name", "")
        if not os.path.isdir(os.path.join(PLANS_DIR, pname)):
            return jsonify({"ok": False, "error": f"Plan '{pname}' not found"}), 404
        item.setdefault("repeat", 1)
    with _queue_lock:
        _run_queue.clear()
        _run_queue.extend(items)
        _queue_delay_s = delay
    return jsonify({"ok": True, "queue_length": len(_run_queue)})


@app.route("/api/queue", methods=["DELETE"])
def clear_queue():
    """Clear the run queue and abort if running."""
    with _queue_lock:
        _run_queue.clear()
    if _queue_running.is_set():
        _queue_running.clear()
        if _plan_running.is_set():
            _plan_running.clear()
            sid = _plan_sid
            if sid:
                with _active_lock:
                    procs = _active_procs.pop(sid, [])
                for p in procs:
                    if p and p.poll() is None:
                        p.kill()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Plan execution — single experiment runner
# ---------------------------------------------------------------------------

def _run_ping_for_plan(session: BenchmarkSession, exp: dict) -> List[dict]:
    """Run a ping experiment for the plan executor. Returns a list of ping row dicts."""
    count = int(exp.get("ping_count", 60))
    interval = max(float(exp.get("ping_interval", 0.5)), 0.1)
    packet_size = max(8, min(int(exp.get("ping_packet_size", 56)), 65507))
    target_ip = session.target_ip

    ping_cmd = [
        "ip", "netns", "exec", session.ns,
        "ping", "-c", str(count), "-i", str(interval), "-s", str(packet_size),
        target_ip,
    ]
    proc = subprocess.Popen(ping_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    with _active_lock:
        if session.sid not in _active_procs:
            _active_procs[session.sid] = []
        _active_procs[session.sid].append(proc)

    ping_rows: List[dict] = []
    seq = 0
    prev_rtt: Optional[float] = None

    for line in iter(proc.stdout.readline, ""):
        if proc.poll() is not None and not line:
            break
        line = line.strip()
        m_rtt = re.search(r"time[=<]([\d.]+)\s*ms", line)
        m_seq = re.search(r"icmp_seq=(\d+)", line)
        m_ttl = re.search(r"ttl=(\d+)", line)
        if m_rtt:
            seq = int(m_seq.group(1)) if m_seq else seq + 1
            rtt = float(m_rtt.group(1))
            jitter = round(abs(rtt - prev_rtt), 3) if prev_rtt is not None else 0.0
            prev_rtt = rtt
            ping_rows.append({
                "seq": seq,
                "rtt_ms": rtt,
                "owd_ms": round(rtt / 2, 3),
                "jitter_ms": jitter,
                "ttl": int(m_ttl.group(1)) if m_ttl else None,
            })
            socketio.emit("ping_data", {
                "seq": seq, "ms": rtt,
                "ttl": int(m_ttl.group(1)) if m_ttl else None,
            }, to=session.sid)

    proc.wait()
    with _active_lock:
        if session.sid in _active_procs and proc in _active_procs[session.sid]:
            _active_procs[session.sid].remove(proc)

    return ping_rows


def _run_single_experiment(session: BenchmarkSession, exp: dict):
    """Execute one plan experiment. Returns (phase_results, ping_rows)."""
    test_type = exp.get("type", "throughput")
    direction = exp.get("direction", "ul") or "ul"
    duration = int(exp.get("duration") or 15)

    if test_type == "latency":
        ping_rows = _run_ping_for_plan(session, exp)
        return [], ping_rows

    # throughput or bufferbloat
    phase_results = []
    if direction == "both":
        half_dur = max(duration // 2, 3)
        phase_results.append(run_iperf_phase(session, "dl", half_dur))
        time.sleep(1)
        phase_results.append(run_iperf_phase(session, "ul", half_dur))
    else:
        phase_results.append(run_iperf_phase(session, direction, duration))
    return phase_results, []


# ---------------------------------------------------------------------------
# Plan execution SocketIO thread
# ---------------------------------------------------------------------------

def _plan_execution_thread(sid: str, plan_name: str, resume: bool = False,
                           run_id_override: str = None):
    global _plan_sid
    _plan_running.set()
    _plan_sid = sid

    try:
        plan = _load_plan(plan_name)

        # Determine run_id
        if run_id_override:
            run_id = run_id_override
        elif resume and plan.get("run_id"):
            run_id = plan["run_id"]
        else:
            run_id = datetime.now().strftime("%Y%m%dT%H%M%S")

        # Build run_data — a snapshot of the template experiments with runtime state
        import copy as _copy
        run_experiments = _copy.deepcopy(plan.get("experiments", []))

        # Check for existing run.json when resuming
        run_json_path = os.path.join(PLANS_DIR, plan_name, f"run_{run_id}", "run.json")
        if resume and os.path.exists(run_json_path):
            with open(run_json_path) as f:
                existing_run = json.load(f)
            run_experiments = existing_run.get("experiments", run_experiments)

        if not resume:
            for exp in run_experiments:
                exp["state"] = "pending"
                exp["result_path"] = None
                exp["error"] = None
                exp["started_at"] = None
                exp["completed_at"] = None

        # Reset any stuck "running" experiments from a prior crash
        for exp in run_experiments:
            if exp.get("state") == "running":
                exp["state"] = "failed"
                exp["error"] = "Interrupted (server restart or prior abort)"

        run_data = {
            "run_id": run_id,
            "plan_name": plan_name,
            "started_at": datetime.now().isoformat(),
            "completed_at": None,
            "status": "running",
            "experiments": run_experiments,
        }
        _save_run(plan_name, run_id, run_data)

        # Also keep plan.json updated for backward compat
        plan["run_id"] = run_id
        plan["experiments"] = _copy.deepcopy(run_experiments)
        _save_plan(plan_name, plan)

        config = plan.get("config", {})
        ns = config.get("namespace", "ue1")
        target_ip = config.get("target_ip", "10.45.0.1")

        start_time = time.time()
        completed_durations: List[float] = []
        experiments = run_data["experiments"]
        total = len(experiments)

        for i, exp in enumerate(experiments):
            if not _plan_running.is_set():
                break
            if resume and exp.get("state") == "completed":
                continue

            proto = exp.get("protocol") or "tcp"
            bandwidth = exp.get("bandwidth") or "200M"
            length_bytes = int(exp.get("length_bytes") or 1200)
            session = BenchmarkSession(
                sid=sid, ns=ns, target_ip=target_ip, proto=proto,
                bandwidth=bandwidth, length_bytes=length_bytes,
            )

            exp["state"] = "running"
            exp["started_at"] = datetime.now().isoformat()
            _save_run(plan_name, run_id, run_data)

            # Estimate remaining time
            avg_dur = (
                sum(completed_durations) / len(completed_durations)
                if completed_durations
                else int(exp.get("duration") or 15)
            )
            remaining_exps = sum(
                1 for e in experiments[i:] if e.get("state") not in ("completed",)
            )
            estimated_remaining = avg_dur * remaining_exps

            socketio.emit("plan_progress", {
                "plan_name": plan_name,
                "run_id": run_id,
                "current_exp": i + 1,
                "total_exp": total,
                "exp_id": exp["id"],
                "exp_name": exp.get("label", exp["id"]),
                "exp_state": "running",
                "elapsed_s": round(time.time() - start_time, 1),
                "estimated_remaining_s": round(estimated_remaining, 0),
            }, to=sid)

            exp_start = time.time()
            try:
                safe_bw = (exp.get("bandwidth") or "").replace("/", "").replace("\\", "")
                result_subpath = (
                    f"plans/{plan_name}/run_{run_id}/"
                    f"{exp['id']}_{exp.get('type','tp')}_"
                    f"{exp.get('direction','ul')}_{exp.get('protocol','tcp')}"
                    + (f"_{safe_bw}" if safe_bw else "")
                )
                phase_results, ping_rows = _run_single_experiment(session, exp)

                # Save CSV + metadata (no chart screenshot for plan experiments)
                result_dir = os.path.join(RESULTS_DIR, result_subpath)
                os.makedirs(result_dir, exist_ok=True)

                csv_written = False
                if phase_results:
                    _write_iperf_csv(os.path.join(result_dir, "data.csv"), phase_results)
                    _write_raw_iperf_output(os.path.join(result_dir, "iperf3_output.txt"), phase_results)
                    csv_written = True
                elif ping_rows:
                    _write_ping_csv(os.path.join(result_dir, "data.csv"), ping_rows)
                    csv_written = True

                # Build structured summary
                summary: Dict[str, Any] = {}
                for ph in phase_results:
                    pfx = ph.get("phase", "")
                    for key in ("client_mbps", "server_mbps", "loss_pct", "jitter_ms", "total_retr"):
                        if ph.get(key) is not None:
                            summary[f"{pfx}_{key}"] = ph[key]
                if ping_rows:
                    rtts = [r["rtt_ms"] for r in ping_rows]
                    summary["avg_rtt_ms"] = round(sum(rtts) / len(rtts), 2)
                    summary["min_rtt_ms"] = round(min(rtts), 2)
                    summary["max_rtt_ms"] = round(max(rtts), 2)
                    summary["packets"] = len(ping_rows)

                meta = {
                    "test_type": exp.get("type"),
                    "direction": exp.get("direction"),
                    "protocol": exp.get("protocol"),
                    "bandwidth": exp.get("bandwidth"),
                    "namespace": ns,
                    "target_ip": target_ip,
                    "duration_s": exp.get("duration"),
                    "timestamp": exp["started_at"],
                    "chart": None,
                    "csv": "data.csv" if csv_written else None,
                    "raw_output": "iperf3_output.txt" if phase_results else None,
                    "summary": summary,
                    "plan_name": plan_name,
                    "exp_id": exp["id"],
                }
                with open(os.path.join(result_dir, "metadata.json"), "w") as f:
                    json.dump(meta, f, indent=2)

                exp["state"] = "completed"
                exp["result_path"] = result_subpath
                exp["completed_at"] = datetime.now().isoformat()

            except Exception as e:
                exp["state"] = "failed"
                exp["error"] = str(e)
                exp["completed_at"] = datetime.now().isoformat()

            completed_durations.append(time.time() - exp_start)
            _save_run(plan_name, run_id, run_data)
            # Also keep plan.json in sync for backward compat
            import copy as _copy2
            plan["experiments"] = _copy2.deepcopy(run_data["experiments"])
            _save_plan(plan_name, plan)

            # Progress update after completion
            remaining_after = sum(
                1 for e in experiments[i + 1:] if e.get("state") not in ("completed",)
            )
            socketio.emit("plan_progress", {
                "plan_name": plan_name,
                "run_id": run_id,
                "current_exp": i + 1,
                "total_exp": total,
                "exp_id": exp["id"],
                "exp_name": exp.get("label", exp["id"]),
                "exp_state": exp["state"],
                "elapsed_s": round(time.time() - start_time, 1),
                "estimated_remaining_s": round(
                    (sum(completed_durations) / len(completed_durations)) * remaining_after, 0
                ) if completed_durations else 0,
            }, to=sid)

            if not _plan_running.is_set():
                break

            time.sleep(2)  # inter-experiment pause

        completed = sum(1 for e in experiments if e.get("state") == "completed")
        failed = sum(1 for e in experiments if e.get("state") == "failed")
        run_data["completed_at"] = datetime.now().isoformat()
        run_data["status"] = "completed" if failed == 0 else "partial"
        _save_run(plan_name, run_id, run_data)

        socketio.emit("plan_complete", {
            "plan_name": plan_name,
            "run_id": run_id,
            "status": "done",
            "completed": completed,
            "failed": failed,
        }, to=sid)

    except Exception as exc:
        socketio.emit("plan_complete", {
            "plan_name": plan_name, "status": "error", "message": str(exc),
        }, to=sid)

    finally:
        _plan_running.clear()
        _plan_sid = None
        with _active_lock:
            procs = _active_procs.pop(sid, [])
            for p in procs:
                if p and p.poll() is None:
                    p.kill()


@socketio.on("start_plan")
def handle_start_plan(data):
    sid = request.sid
    plan_name = data.get("plan_name", "").strip()
    resume = bool(data.get("resume", False))

    if not plan_name:
        emit("plan_complete", {"status": "error", "message": "No plan name provided"})
        return

    if not os.path.isdir(os.path.join(PLANS_DIR, plan_name)):
        emit("plan_complete", {"status": "error", "message": f"Plan '{plan_name}' not found"})
        return

    if _plan_running.is_set():
        emit("plan_complete", {"status": "error", "message": "Another plan is already running"})
        return

    t = threading.Thread(
        target=_plan_execution_thread, args=(sid, plan_name, resume), daemon=True
    )
    t.start()


@socketio.on("stop_plan")
def handle_stop_plan(data=None):
    _plan_running.clear()
    sid = request.sid
    with _active_lock:
        procs = _active_procs.pop(sid, [])
    for p in procs:
        if p and p.poll() is None:
            p.kill()
    emit("plan_complete", {"plan_name": "", "status": "stopped", "message": "Plan aborted by user."})


# ---------------------------------------------------------------------------
# Queue execution
# ---------------------------------------------------------------------------

def _queue_execution_thread(sid: str):
    """Process the run queue: execute plans sequentially with delays."""
    _queue_running.set()
    try:
        queue_idx = 0
        while _queue_running.is_set():
            with _queue_lock:
                if not _run_queue:
                    break
                item = _run_queue[0]

            plan_name = item["plan_name"]
            repeat = int(item.get("repeat", 1))
            completed_repeats = int(item.get("_completed", 0))

            if completed_repeats >= repeat:
                with _queue_lock:
                    if _run_queue and _run_queue[0] is item:
                        _run_queue.pop(0)
                continue

            # Emit queue progress
            with _queue_lock:
                total_items = len(_run_queue)
            socketio.emit("queue_progress", {
                "queue_position": queue_idx + 1,
                "queue_total": total_items + queue_idx,
                "plan_name": plan_name,
                "repeat_current": completed_repeats + 1,
                "repeat_total": repeat,
                "status": "running",
            }, to=sid)

            # Run the plan (blocks until done)
            _plan_execution_thread(sid, plan_name, resume=False)

            # Wait for plan to finish (it clears _plan_running in its finally block)
            # Mark repeat as completed
            item["_completed"] = completed_repeats + 1
            if item["_completed"] >= repeat:
                with _queue_lock:
                    if _run_queue and _run_queue[0] is item:
                        _run_queue.pop(0)
                queue_idx += 1

            if not _queue_running.is_set():
                break

            # Delay between runs
            with _queue_lock:
                if _run_queue:
                    delay = _queue_delay_s
                else:
                    delay = 0
            for _ in range(delay):
                if not _queue_running.is_set():
                    break
                time.sleep(1)

        socketio.emit("queue_complete", {
            "status": "done",
            "message": "All queued runs completed.",
        }, to=sid)
    except Exception as exc:
        socketio.emit("queue_complete", {
            "status": "error",
            "message": str(exc),
        }, to=sid)
    finally:
        _queue_running.clear()


@socketio.on("start_queue")
def handle_start_queue(data=None):
    sid = request.sid
    with _queue_lock:
        if not _run_queue:
            emit("queue_complete", {"status": "error", "message": "Queue is empty"})
            return
    if _queue_running.is_set() or _plan_running.is_set():
        emit("queue_complete", {"status": "error", "message": "A plan or queue is already running"})
        return
    t = threading.Thread(target=_queue_execution_thread, args=(sid,), daemon=True)
    t.start()


@socketio.on("stop_queue")
def handle_stop_queue(data=None):
    _queue_running.clear()
    _plan_running.clear()
    sid = request.sid
    with _active_lock:
        procs = _active_procs.pop(sid, [])
    for p in procs:
        if p and p.poll() is None:
            p.kill()
    with _queue_lock:
        _run_queue.clear()
    emit("queue_complete", {"status": "stopped", "message": "Queue aborted by user."})


# ---------------------------------------------------------------------------
# Startup cleanup
# ---------------------------------------------------------------------------

def _cleanup_stuck_plans():
    """Reset any experiment stuck in 'running' state from a prior server crash."""
    if not os.path.isdir(PLANS_DIR):
        return
    for plan_name in os.listdir(PLANS_DIR):
        plan_json = os.path.join(PLANS_DIR, plan_name, "plan.json")
        if not os.path.exists(plan_json):
            continue
        try:
            with open(plan_json) as f:
                plan = json.load(f)
            changed = False
            for exp in plan.get("experiments", []):
                if exp.get("state") == "running":
                    exp["state"] = "failed"
                    exp["error"] = "Interrupted (server restart)"
                    changed = True
            if changed:
                _save_plan(plan_name, plan)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("WARNING: Run as root (sudo) for ip netns commands.")
    _cleanup_stuck_plans()
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, allow_unsafe_werkzeug=True)

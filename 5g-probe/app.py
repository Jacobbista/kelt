"""
5G UE Probe — Flask-SocketIO Backend (V3)

REST APIs for infrastructure:
- /api/status, /api/isolate, /api/reset, /api/benchmark (quick)

SocketIO events for live streaming:
- start_live_benchmark → streams iperf_data / ping_data in real-time
- stop_live_benchmark → kills running processes

Must be run as root (sudo).
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config["SECRET_KEY"] = "5g-probe-secret"
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

# Directory where test results (screenshots + metadata) are saved
RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

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
    prefix = mac[:8].lower()
    return KNOWN_OUI.get(prefix, "Unknown")


def list_usb_ifaces() -> List[Dict[str, str]]:
    out = _run_capture(["ip", "-br", "link", "show"])
    ifaces: List[Dict[str, str]] = []
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0].startswith("enx"):
            name = parts[0]
            state = parts[1] if len(parts) > 1 else "UNKNOWN"
            mac = _get_mac(name)
            vendor = _identify_vendor(mac)
            role = "router" if vendor == "Realtek" else "ue"
            ifaces.append({"name": name, "state": state, "mac": mac, "vendor": vendor, "role": role})
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

    ns = next_namespace_name()
    logs: List[str] = []

    try:
        _run(["ip", "netns", "add", ns], check=False)
        logs.append(f"Namespace '{ns}' created.")

        _run(["ip", "link", "set", iface, "netns", ns])
        logs.append(f"Interface '{iface}' → '{ns}'.")

        _run(["ip", "netns", "exec", ns, "ip", "link", "set", "lo", "up"])
        _run(["ip", "netns", "exec", ns, "ip", "link", "set", iface, "up"])
        logs.append("Loopback + interface UP.")

        if shutil.which("dhclient"):
            _run(["ip", "netns", "exec", ns, "dhclient", "-r", iface], check=False)
            _run(["ip", "netns", "exec", ns, "dhclient", "-v", iface], check=False)
            logs.append("DHCP lease acquired.")
        else:
            logs.append("WARNING: dhclient not found.")

        _run(["ip", "netns", "exec", ns, "ip", "route", "add", "default", "dev", iface], check=False)
        logs.append(f"Default route via '{iface}'.")

        port = start_webui_tunnel(ns)
        if port:
            logs.append(f"WebUI tunnel: http://localhost:{port}")

        return jsonify({"status": "success", "message": f"'{iface}' isolated → '{ns}'.",
                        "data": {"namespace": ns, "interface": iface, "webui_port": port, "logs": logs}})
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
# SocketIO — Live Benchmark
# ---------------------------------------------------------------------------

def _live_benchmark_thread(sid: str, ns: str, target_ip: str, duration: int, mode: str,
                           proto: str = "tcp", bandwidth: str = "200M", with_ping: bool = True):
    """Background thread: runs iperf3 (optionally + ping) and streams data via SocketIO.

    proto: 'tcp' or 'udp'
    bandwidth: target bandwidth for UDP (e.g. '200M', '1G')
    with_ping: if True, run a concurrent ping (needed for bufferbloat tests)
    """

    ping_cmd = ["ip", "netns", "exec", ns, "ping", target_ip]
    tests_done = threading.Event()
    ping_proc = None
    t_ping = None
    second_counter = {"val": 0}

    try:
        if with_ping:
            ping_proc = subprocess.Popen(ping_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
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


        def run_iperf_phase(phase_mode, phase_duration):
            """Run one iperf3 phase and return summary dict."""
            reverse_flag = ["-R"] if phase_mode == "dl" else []
            udp_flags = ["-u", "-b", bandwidth] if proto == "udp" else []
            # We use -i 0.5 to get twice as many data points for the live chart
            # The frontend will apply a moving average to smooth the TCP sawtooth
            iperf_cmd = [
                "ip", "netns", "exec", ns,
                "iperf3", "-c", target_ip,
                "-t", str(phase_duration), "-i", "0.5",
                "-f", "m", "--forceflush", "--get-server-output"
            ] + udp_flags + reverse_flag

            # In UDP mode: DL (-R) → client is receiver (accurate interval stats)
            #              UL (no -R) → client is sender (interval = injected, NOT received)
            is_sender_side = proto == "udp" and phase_mode != "dl"

            proto_label = f"UDP ({bandwidth})" if proto == "udp" else "TCP"
            socketio.emit("test_status", {"status": "running",
                "message": f"Running {phase_mode.upper()} [{proto_label}] for {phase_duration}s…"}, to=sid)

            iperf_proc = subprocess.Popen(iperf_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

            with _active_lock:
                if sid not in _active_procs:
                    _active_procs[sid] = []
                _active_procs[sid].append(iperf_proc)

            phase_summary = {"phase": phase_mode, "proto": proto, "sender_side": is_sender_side, "server_intervals": []}
            total_retr = 0  # accumulate TCP retransmits across the phase
            in_server_output = False

            for line in iter(iperf_proc.stdout.readline, ""):
                if iperf_proc.poll() is not None and not line:
                    break
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
                            # Parse loss from receiver summary: e.g. "62776/173445 (36%)"
                            lm = re.search(r"(\d+)/(\d+)\s*\(([\d.]+)%\)", line)
                            if lm:
                                phase_summary["lost"] = int(lm.group(1))
                                phase_summary["total"] = int(lm.group(2))
                                phase_summary["loss_pct"] = float(lm.group(3))
                            # Parse jitter
                            jm = re.search(r"([\d.]+)\s*ms", line)
                            if jm:
                                phase_summary["jitter_ms"] = float(jm.group(1))
                        else:
                            # Only capture sender summary if we are NOT in the server output block
                            # (though server output usually doesn't have a sender summary anyway)
                            if not in_server_output:
                                phase_summary["sender_mbps"] = mbps_val
                                # TCP sender summary has total retransmits
                                rm = re.search(r"Mbits/sec\s+(\d+)\s", line)
                                if rm:
                                    phase_summary["total_retr"] = int(rm.group(1))
                    continue  # don't push summary lines to the live chart or arrays

                # --- Normal interval lines ---
                m = re.search(r"([\d.]+)\s*Mbits/sec", line)
                if m:
                    mbps = float(m.group(1))
                    
                    if in_server_output:
                        # We are capturing the real receiver intervals at the end of the test
                        # to overwrite the frontend chart
                        phase_summary["server_intervals"].append({"mbps": mbps})
                    else:
                        # Standard live charting output
                        second_counter["val"] += 0.5
    
                        evt = {"mbps": mbps, "second": second_counter["val"],
                               "proto": proto, "phase": phase_mode,
                               "sender_side": is_sender_side, "raw": line}
    
                        # Parse jitter & loss from UDP receiver-side interval lines
                        if proto == "udp" and not is_sender_side:
                            jm = re.search(r"Mbits/sec\s+([\d.]+)\s*ms", line)
                            if jm:
                                evt["jitter_ms"] = float(jm.group(1))
                            lm = re.search(r"(\d+)/(\d+)\s*\(([\d.]+)%\)", line)
                            if lm:
                                evt["loss_pct"] = float(lm.group(3))
                                evt["lost"] = int(lm.group(1))
                                evt["total"] = int(lm.group(2))
    
                        # Parse retransmits & cwnd from TCP sender interval lines
                        if proto == "tcp":
                            rm = re.search(r"Mbits/sec\s+(\d+)\s+([\d.]+)\s*(KBytes|MBytes)", line)
                            if rm:
                                retr = int(rm.group(1))
                                cwnd_val = float(rm.group(2))
                                unit = rm.group(3)
                                
                                cwnd_kb = cwnd_val * 1024 if unit == "MBytes" else cwnd_val
                                
                                evt["retr"] = retr
                                evt["cwnd_kb"] = cwnd_kb
                                total_retr += retr
    
                        socketio.emit("iperf_data", evt, to=sid)

            iperf_proc.wait()
            with _active_lock:
                if sid in _active_procs and iperf_proc in _active_procs[sid]:
                    _active_procs[sid].remove(iperf_proc)

            return phase_summary


        socketio.emit("test_status", {"status": "starting", "message": "Launching benchmark..."}, to=sid)

        phase_results = []
        if mode == "both":
            half_dur = max(duration // 2, 3)
            phase_results.append(run_iperf_phase("dl", half_dur))
            import time
            time.sleep(1)
            phase_results.append(run_iperf_phase("ul", half_dur))
        else:
            phase_results.append(run_iperf_phase(mode, duration))

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
            "phases": phase_results
        }, to=sid)

    except Exception as exc:
        socketio.emit("test_complete", {"status": "error", "message": str(exc)}, to=sid)

    finally:
        tests_done.set()
        # Cleanup
        with _active_lock:
            procs = _active_procs.pop(sid, [])
            for p in procs:
                if p and p.poll() is None:
                    p.kill()


@socketio.on("start_live_benchmark")
def handle_start_live_benchmark(data):
    """Client requests a live benchmark. Spawns a background thread."""
    sid = request.sid
    ns = data.get("namespace", "ue1")
    target_ip = data.get("target_ip", "10.45.0.1")
    duration = min(int(data.get("duration", 10)), 120)
    mode = data.get("mode", "dl")          # "dl", "ul", or "both"
    proto = data.get("proto", "tcp")       # "tcp" or "udp"
    bandwidth = data.get("bandwidth", "200M")  # UDP target bandwidth
    with_ping = bool(data.get("with_ping", False))  # enable concurrent ping

    with _active_lock:
        for p in _active_procs.get(sid, []):
            if p and p.poll() is None:
                p.kill()
        _active_procs.pop(sid, None)

    t = threading.Thread(
        target=_live_benchmark_thread,
        args=(sid, ns, target_ip, duration, mode, proto, bandwidth, with_ping),
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

def _live_ping_thread(sid: str, ns: str, target_ip: str, count: int, interval: float):
    """Background thread: runs ping, parses per-packet RTT and emits ping_data."""
    ping_cmd = [
        "ip", "netns", "exec", ns,
        "ping", "-c", str(count), "-i", str(interval), target_ip,
    ]

    proc = None
    try:
        socketio.emit("test_status", {"status": "starting", "message": f"Pinging {target_ip} ({count} packets)..."}, to=sid)

        proc = subprocess.Popen(ping_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        with _active_lock:
            _active_procs[sid] = [proc]

        socketio.emit("test_status", {"status": "running", "message": f"Live ping running..."}, to=sid)

        seq = 0
        for line in iter(proc.stdout.readline, ""):
            if proc.poll() is not None and not line:
                break
            line = line.strip()
            # Per-packet: "64 bytes from 10.45.0.1: icmp_seq=1 ttl=64 time=12.3 ms"
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

        # Parse summary from final output
        remaining = proc.stdout.read() if proc.stdout else ""
        full_out = remaining  # already consumed line by line, just for regex on final stats
        # Re-run a quick summary ping if needed is complex; emit stats from what we have
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
    ns = data.get("namespace", "ue1")
    target_ip = data.get("target_ip", "10.45.0.1")
    count = min(int(data.get("count", 60)), 3000)
    interval = max(float(data.get("interval", 0.5)), 0.2)

    # Kill any existing test
    with _active_lock:
        for p in _active_procs.get(sid, []):
            if p and p.poll() is None:
                p.kill()
        _active_procs.pop(sid, None)

    t = threading.Thread(target=_live_ping_thread, args=(sid, ns, target_ip, count, interval), daemon=True)
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
# Test Results — save & browse
# ---------------------------------------------------------------------------

@app.route("/api/save_result", methods=["POST"])
def save_result():
    """Save a test result screenshot + metadata."""
    try:
        data = request.json
        image_b64 = data.get("image", "")  # data:image/png;base64,...
        test_type = data.get("test_type", "unknown")
        direction = data.get("direction", "")
        protocol = data.get("protocol", "")
        summary = data.get("summary", "")

        now = datetime.now()
        date_dir = os.path.join(RESULTS_DIR, now.strftime("%Y-%m-%d"))
        os.makedirs(date_dir, exist_ok=True)

        parts = [test_type]
        if direction:
            parts.append(direction)
        if protocol:
            parts.append(protocol)
        basename = now.strftime("%H-%M-%S") + "_" + "_".join(parts)

        # Save PNG
        if "," in image_b64:
            image_b64 = image_b64.split(",", 1)[1]
        png_path = os.path.join(date_dir, basename + ".png")
        with open(png_path, "wb") as f:
            f.write(base64.b64decode(image_b64))

        # Save metadata JSON
        meta = {
            "test_type": test_type,
            "direction": direction,
            "protocol": protocol,
            "summary": summary,
            "timestamp": now.isoformat(),
            "image": basename + ".png",
        }
        with open(os.path.join(date_dir, basename + ".json"), "w") as f:
            json.dump(meta, f, indent=2)

        return jsonify({"ok": True, "path": f"{now.strftime('%Y-%m-%d')}/{basename}.png"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/results")
def list_results():
    """List all saved test results grouped by date."""
    import collections
    results = collections.OrderedDict()
    if not os.path.isdir(RESULTS_DIR):
        return jsonify(results)
    for date_folder in sorted(os.listdir(RESULTS_DIR), reverse=True):
        date_path = os.path.join(RESULTS_DIR, date_folder)
        if not os.path.isdir(date_path):
            continue
        items = []
        for fname in sorted(os.listdir(date_path), reverse=True):
            if fname.endswith(".json"):
                with open(os.path.join(date_path, fname)) as f:
                    meta = json.load(f)
                meta["date"] = date_folder
                items.append(meta)
        if items:
            results[date_folder] = items
    return jsonify(results)


@app.route("/api/results/<date>/<filename>")
def serve_result(date, filename):
    """Serve a saved result image."""
    date_path = os.path.join(RESULTS_DIR, date)
    return send_from_directory(date_path, filename)


@app.route("/api/results/<date>/<filename>", methods=["DELETE"])
def delete_result(date, filename):
    """Delete a saved result (PNG and JSON)."""
    try:
        date_path = os.path.join(RESULTS_DIR, date)
        png_path = os.path.join(date_path, filename)
        json_path = os.path.join(date_path, filename.replace(".png", ".json"))
        
        if os.path.exists(png_path):
            os.remove(png_path)
        if os.path.exists(json_path):
            os.remove(json_path)
            
        # Clean up empty date folder if no more results
        if os.path.isdir(date_path) and not os.listdir(date_path):
            os.rmdir(date_path)
            
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/open_folder/<date>", methods=["POST"])
def open_result_folder(date):
    """Open the specific results folder on the host machine using nautilus or xdg-open."""
    try:
        date_path = os.path.join(RESULTS_DIR, date)
        if os.path.isdir(date_path):
            # Use Popen to launch it detached, avoiding inherited terminal themes or blocking
            env = os.environ.copy()
            # Clear some env vars that might mess up xdg-open theming if running under sudo
            if "SUDO_USER" in env:
                # if running as root via sudo, xdg-open might open as root with weird theme
                user = env["SUDO_USER"]
                subprocess.Popen(["su", "-", user, "-c", f"xdg-open {date_path}"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", date_path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "Folder not found"}), 404
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("WARNING: Run as root (sudo) for ip netns commands.")
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, allow_unsafe_werkzeug=True)

"""
5G UE Probe — Flask-SocketIO Backend (V4)

REST APIs for infrastructure:
- /api/status, /api/isolate, /api/reset, /api/benchmark (quick)
- /api/plans  (plan management CRUD)

SocketIO events for live streaming:
- start_live_benchmark → streams iperf_data in real-time
- start_live_ping       → streams ping_data in real-time
- start_plan / stop_plan → sequential experiment execution with progress events (non-resume: payload must include ``namespace`` and ``target_ip``; they are stored in ``run.json``)

Must be run as root (sudo).
"""

from __future__ import annotations

import base64
import copy
import csv
import ipaddress
import json
import os
import pwd
import re
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_socketio import SocketIO, emit
from werkzeug.exceptions import HTTPException

from probe import config as probe_config
from probe import net_diag

app = Flask(
    __name__,
    template_folder=os.path.join(probe_config.PACKAGE_ROOT, "templates"),
)
app.config["SECRET_KEY"] = "5g-probe-secret"
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")


@app.errorhandler(HTTPException)
def _http_errors_json_for_api(exc: HTTPException):
    """API callers use fetch().json(); return JSON instead of HTML error pages for /api/*."""
    if request.path.startswith("/api/"):
        detail = exc.description or getattr(exc, "name", None) or str(exc.code or "")
        return jsonify({"ok": False, "error": detail}), exc.code or 500
    return exc.get_response()


RESULTS_DIR = os.path.join(probe_config.PACKAGE_ROOT, "results")
LEGACY_PLANS_DIR = os.path.join(RESULTS_DIR, "plans")
PLAN_TEMPLATES_DIR = os.path.join(probe_config.PACKAGE_ROOT, "plan_templates")
PLAN_DEFAULTS_DIR = os.path.join(PLAN_TEMPLATES_DIR, "defaults")
PLAN_RUNS_DIR = os.path.join(RESULTS_DIR, "plan_runs")
PLAN_LAYOUT_MIGRATION_STAMP = os.path.join(RESULTS_DIR, ".plan_layout_v2")
# Slug ``defaults`` would collide with ``plan_templates/defaults/`` package layout.
RESERVED_PLAN_SLUGS = frozenset({"defaults"})

WEBUI_TUNNEL_STATE_FILE = os.path.join(RESULTS_DIR, "webui_tunnel_state.json")
USER_BENCHMARK_TARGETS_JSON = os.path.join(RESULTS_DIR, "user_benchmark_targets.json")
os.makedirs(RESULTS_DIR, exist_ok=True)
os.makedirs(PLAN_TEMPLATES_DIR, exist_ok=True)
os.makedirs(PLAN_DEFAULTS_DIR, exist_ok=True)
os.makedirs(PLAN_RUNS_DIR, exist_ok=True)

BUILTIN_PLAN_SRC = os.path.join(PLAN_DEFAULTS_DIR, "standard_iperf_smoke", "plan.json")
# Built-ins under ``plan_templates/defaults/`` — API read-only; Duplicate only in UI.
READONLY_BUILTIN_PLANS = frozenset({"standard_iperf_smoke"})

# ---------------------------------------------------------------------------
# Ownership helpers — restore real-user ownership when running under sudo
# ---------------------------------------------------------------------------

_REAL_UID: Optional[int] = int(os.environ["SUDO_UID"]) if "SUDO_UID" in os.environ else None
_REAL_GID: Optional[int] = int(os.environ["SUDO_GID"]) if "SUDO_GID" in os.environ else None


def _chown_result(path: str) -> None:
    """Chown a single path to the real user (no-op if not running under sudo)."""
    if _REAL_UID is None:
        return
    try:
        os.chown(path, _REAL_UID, _REAL_GID)
    except OSError:
        pass


def _makedirs_chown(path: str) -> None:
    """makedirs + chown the new path and all intermediate dirs under RESULTS_DIR."""
    os.makedirs(path, exist_ok=True)
    if _REAL_UID is None:
        return
    try:
        rel = os.path.relpath(path, RESULTS_DIR)
        if rel.startswith(".."):
            return
        cur = RESULTS_DIR
        _chown_result(cur)
        for part in rel.split(os.sep):
            if part in ("", "."):
                continue
            cur = os.path.join(cur, part)
            _chown_result(cur)
    except Exception:
        pass


def _fix_results_ownership() -> None:
    """Recursively chown results/ to the real user. Called once at startup."""
    if _REAL_UID is None:
        return
    for dirpath, _dirs, filenames in os.walk(RESULTS_DIR):
        _chown_result(dirpath)
        for fname in filenames:
            _chown_result(os.path.join(dirpath, fname))
_USER_BENCHMARK_TARGET_CAP = 64


def _valid_benchmark_ip(ip: str) -> bool:
    raw = (ip or "").strip()
    if not raw:
        return False
    try:
        ipaddress.ip_address(raw)
    except ValueError:
        return False
    return True


def _load_user_benchmark_targets() -> List[dict]:
    if not os.path.isfile(USER_BENCHMARK_TARGETS_JSON):
        return []
    try:
        with open(USER_BENCHMARK_TARGETS_JSON, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        out: List[dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            iid = str(item.get("id") or "").strip()
            lab = str(item.get("label") or "").strip()
            tip = str(item.get("ip") or "").strip()
            if not iid or not lab or not _valid_benchmark_ip(tip):
                continue
            if not iid.startswith("user_"):
                iid = f"user_{iid.lstrip('user_')}"
            out.append({"id": iid, "label": lab[:120], "ip": tip})
        return out
    except Exception:
        return []


def _save_user_benchmark_targets(items: List[dict]) -> None:
    with open(USER_BENCHMARK_TARGETS_JSON, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2)
    _chown_result(USER_BENCHMARK_TARGETS_JSON)


def _merged_benchmark_targets() -> List[dict]:
    return list(probe_config.benchmark_targets()) + _load_user_benchmark_targets()


def _plan_template_dir(plan_name: str) -> str:
    """Repo defaults: ``plan_templates/defaults/<slug>/``. User saves: ``plan_templates/<slug>/``."""
    d_defaults = os.path.join(PLAN_DEFAULTS_DIR, plan_name)
    if os.path.isfile(os.path.join(d_defaults, "plan.json")):
        return d_defaults
    return os.path.join(PLAN_TEMPLATES_DIR, plan_name)


def _plan_template_json(plan_name: str) -> str:
    return os.path.join(_plan_template_dir(plan_name), "plan.json")


def _rewrite_result_paths_json_file(path: str) -> None:
    """Rewrite legacy ``plans/`` URL prefixes to ``plan_runs/`` inside a JSON file."""
    try:
        with open(path, encoding="utf-8") as f:
            txt = f.read()
    except OSError:
        return
    if "plans/" not in txt:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(txt.replace("plans/", "plan_runs/"))
    except OSError:
        pass


def _migrate_legacy_plans_layout() -> None:
    """One-time: ``results/plans`` → templates under ``plan_templates`` + runs under ``results/plan_runs``."""
    if os.path.isfile(PLAN_LAYOUT_MIGRATION_STAMP):
        return
    legacy = LEGACY_PLANS_DIR
    if not os.path.isdir(legacy):
        try:
            with open(PLAN_LAYOUT_MIGRATION_STAMP, "w", encoding="utf-8") as f:
                f.write("ok\n")
            _chown_result(PLAN_LAYOUT_MIGRATION_STAMP)
        except OSError:
            pass
        return
    for name in list(os.listdir(legacy)):
        src_root = os.path.join(legacy, name)
        if not os.path.isdir(src_root):
            continue
        src_plan = os.path.join(src_root, "plan.json")
        tmpl_json = _plan_template_json(name)
        tmpl_parent = os.path.dirname(tmpl_json)
        runs_root = os.path.join(PLAN_RUNS_DIR, name)
        os.makedirs(runs_root, exist_ok=True)
        if os.path.isfile(src_plan):
            os.makedirs(tmpl_parent, exist_ok=True)
            if not os.path.isfile(tmpl_json):
                shutil.copy2(src_plan, tmpl_json)
                _rewrite_result_paths_json_file(tmpl_json)
            try:
                os.remove(src_plan)
            except OSError:
                pass
        for entry in list(os.listdir(src_root)):
            if entry.startswith("run_") and os.path.isdir(os.path.join(src_root, entry)):
                src_run = os.path.join(src_root, entry)
                dst_run = os.path.join(runs_root, entry)
                if not os.path.exists(dst_run):
                    shutil.move(src_run, dst_run)
                rj = os.path.join(dst_run, "run.json")
                if os.path.isfile(rj):
                    _rewrite_result_paths_json_file(rj)
        shutil.rmtree(src_root, ignore_errors=True)
    try:
        if os.path.isdir(legacy) and not os.listdir(legacy):
            os.rmdir(legacy)
    except OSError:
        pass
    try:
        with open(PLAN_LAYOUT_MIGRATION_STAMP, "w", encoding="utf-8") as f:
            f.write("migrated\n")
        _chown_result(PLAN_LAYOUT_MIGRATION_STAMP)
    except OSError:
        pass


def _ensure_builtin_plans() -> None:
    """Migrate legacy plan layout on startup."""
    _migrate_legacy_plans_layout()


def _iter_plan_template_slugs() -> List[str]:
    names: List[str] = []
    seen = set()
    if os.path.isdir(PLAN_DEFAULTS_DIR):
        for name in sorted(os.listdir(PLAN_DEFAULTS_DIR)):
            if os.path.isfile(os.path.join(PLAN_DEFAULTS_DIR, name, "plan.json")):
                seen.add(name)
                names.append(name)
    if os.path.isdir(PLAN_TEMPLATES_DIR):
        for name in sorted(os.listdir(PLAN_TEMPLATES_DIR)):
            if name == "defaults" or name in seen:
                continue
            if os.path.isfile(os.path.join(PLAN_TEMPLATES_DIR, name, "plan.json")):
                names.append(name)
    return names

# ---------------------------------------------------------------------------
# Local constants
# ---------------------------------------------------------------------------
UE_WEBUI_BASE_PORT = 18180

# Browsers send Host: 127.0.0.1:<tunnel>; many modem UIs reject that — proxy rewrites before forwarding into netns.
_HTTP_REQ_LINE_START = re.compile(rb"^(GET|POST|HEAD|PUT|DELETE|OPTIONS|PATCH)\s")
_HOST_HEADER_LINE_RE = re.compile(rb"(?mi)^Host:[^\r\n]*\r\n")
_CONN_HEADER_LINE_RE = re.compile(rb"(?mi)^Connection:[^\r\n]*\r\n")

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

# Last isolate tunnel settings per namespace (mirrored to WEBUI_TUNNEL_STATE_FILE for restart recovery).
_NS_TUNNEL_CFG: Dict[str, Dict[str, Any]] = {}

# Thread + listening socket for localhost modem Web UI proxy (per namespace).
_WEBUI_PROXY_STATE: Dict[str, Dict[str, Any]] = {}

_tunnel_state_io_lock = threading.Lock()

_UE_NS_NAME_RE = re.compile(r"^ue\d+$")

# Rate-limit tunnel RX debug (-d): browsers open many TLS connections; logging each floods stderr.
_tunnel_proxy_dbg_lock = threading.Lock()
_tunnel_proxy_dbg_last_mono: Dict[str, float] = {}
WEBUI_TUNNEL_DEBUG_INTERVAL_S = 8.0

# Run queue
_run_queue: List[dict] = []   # [{plan_name, repeat, namespace, target_ip}]
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
    bandwidth_dl: Optional[str] = None
    length_bytes: int = 1200
    parallel_streams: int = 1
    interval_s: float = 0.1
    udp_length_mode: str = "fixed"  # omit | auto | fixed
    udp_mtu_clamp: int = 1200
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


def _get_default_gw(iface: str, netns: Optional[str] = None) -> Optional[str]:
    """Return the default gateway reachable via a given interface, if any."""
    cmd = ["ip", "-n", netns, "route", "show", "dev", iface] if netns else ["ip", "route", "show", "dev", iface]
    out = _run_capture(cmd)
    m = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None


def collect_iface_diag(
    iface: str,
    *,
    iface_type: str,
    realtek_router_hint: bool,
    netns: Optional[str] = None,
    probe_target: Optional[str] = None,
) -> Dict[str, Any]:
    """Run iproute2 lookups and return merged diagnostics for REST / status."""
    ip_cmd_prefix = ["ip", "-n", netns] if netns else ["ip"]
    addr_out = _run_capture(ip_cmd_prefix + ["-4", "addr", "show", "dev", iface])
    route_out = _run_capture(ip_cmd_prefix + ["route", "show", "dev", iface])
    link_out = _run_capture(ip_cmd_prefix + ["link", "show", "dev", iface])
    route_get_out: Optional[str] = None
    if probe_target and re.match(r"^\d+\.\d+\.\d+\.\d+$", probe_target):
        route_get_out = _run_capture(ip_cmd_prefix + ["route", "get", probe_target])
    return net_diag.diagnostics_dict(
        iface,
        iface_type=iface_type,
        vendor_mac_hint_router=realtek_router_hint,
        addr_show=addr_out,
        route_show_dev=route_out,
        link_show=link_out,
        route_get_target=probe_target,
        route_get_out=route_get_out,
    )


def pick_management_host(diag: Dict[str, Any], fallback: str = "192.168.1.1") -> str:
    cands = diag.get("management_candidates") or []
    return cands[0] if cands else fallback


def list_usb_ifaces() -> List[Dict[str, Any]]:
    """List isolatable host interfaces: USB Ethernet dongles (enx*) and WWAN modems (wwan*)."""
    out = _run_capture(["ip", "-br", "link", "show"])
    ifaces: List[Dict[str, Any]] = []
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
            realtek_hint = vendor == "Realtek" and role == "router"
            diag = collect_iface_diag(
                name, iface_type="usb", realtek_router_hint=realtek_hint,
            )
            entry: Dict[str, Any] = {
                "name": name, "state": state, "mac": mac,
                "vendor": vendor, "role": role, "iface_type": "usb",
                **diag,
            }
            ifaces.append(entry)

        elif name.startswith("wwan"):
            mac = _get_mac(name)
            diag = collect_iface_diag(
                name, iface_type="wwan", realtek_router_hint=False,
            )
            ifaces.append({
                "name": name, "state": state, "mac": mac,
                "vendor": "WWAN Modem", "role": "ue",
                "iface_type": "wwan",
                "current_ip": diag.get("ipv4") or "",
                "gateway": diag.get("gateway") or "",
                **diag,
            })

    return sorted(ifaces, key=lambda x: x["name"])


def list_netns() -> List[str]:
    out = _run_capture(["ip", "netns", "list"])
    nss: List[str] = []
    for line in out.splitlines():
        parts = line.split()
        if parts:
            nss.append(parts[0].strip())
    return sorted(nss)


_NETDEV_NAME_RE = re.compile(r"^[a-z][a-z0-9._@-]{0,14}$")


def _looks_like_netdev(name: str) -> bool:
    """Reject iproute error lines mistakenly parsed as interface names (e.g. first token 'Cannot')."""
    return bool(name) and bool(_NETDEV_NAME_RE.fullmatch(name))


def list_ns_ifaces(ns: str) -> List[str]:
    proc = _run(["ip", "-n", ns, "-br", "link", "show"], check=False, capture=True)
    if proc.returncode != 0:
        return []
    ifaces: List[str] = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if parts and parts[0] != "lo" and _looks_like_netdev(parts[0]):
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


def record_ns_tunnel(
    ns: str,
    mgmt_host: str,
    mgmt_port: int,
    listen_port: Optional[int] = None,
    management_https: Optional[bool] = None,
) -> None:
    prev = dict(_NS_TUNNEL_CFG.get(ns) or {})
    prev.update({"management_host": mgmt_host, "management_port": int(mgmt_port)})
    if listen_port is not None:
        prev["listen_port"] = int(listen_port)
    if management_https is not None:
        prev["management_https"] = bool(management_https)
    _NS_TUNNEL_CFG[ns] = prev
    _save_webui_tunnel_state_file()


def clear_ns_tunnel(ns: str) -> None:
    _NS_TUNNEL_CFG.pop(ns, None)
    _save_webui_tunnel_state_file()


def _save_webui_tunnel_state_file() -> None:
    """Atomic JSON snapshot of `_NS_TUNNEL_CFG` so Web UI proxies can restart after probe reboot."""
    with _tunnel_state_io_lock:
        try:
            os.makedirs(RESULTS_DIR, exist_ok=True)
            tmp_path = WEBUI_TUNNEL_STATE_FILE + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(_NS_TUNNEL_CFG, f, indent=2, sort_keys=True)
            os.replace(tmp_path, WEBUI_TUNNEL_STATE_FILE)
            _chown_result(WEBUI_TUNNEL_STATE_FILE)
        except OSError:
            pass


def _load_webui_tunnel_state_file() -> Dict[str, Dict[str, Any]]:
    with _tunnel_state_io_lock:
        try:
            if not os.path.isfile(WEBUI_TUNNEL_STATE_FILE):
                return {}
            with open(WEBUI_TUNNEL_STATE_FILE, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, dict):
            out[k] = dict(v)
    return out


def _ensure_webui_tunnel_from_cfg(ns: str, cfg: Dict[str, Any]) -> None:
    """Start proxy for `ns` using a persisted (or in-memory) tunnel config dict."""
    if os.geteuid() != 0 or not shutil.which("socat"):
        return
    host = str(cfg.get("management_host") or "").strip()
    if not host:
        return
    try:
        mgmt_port = int(cfg.get("management_port") if cfg.get("management_port") is not None else 80)
    except (TypeError, ValueError):
        mgmt_port = 80
    mgmt_https = bool(cfg.get("management_https"))
    if not mgmt_https and mgmt_port == 443:
        mgmt_https = True
    record_ns_tunnel(ns, host, mgmt_port, management_https=mgmt_https)
    lp = start_webui_tunnel(ns, host, mgmt_port)
    if lp:
        record_ns_tunnel(ns, host, mgmt_port, listen_port=lp, management_https=mgmt_https)
    else:
        print(
            f"[5g-probe] WARN: could not bind Web UI tunnel on restart for {ns} "
            f"({host}:{mgmt_port}).",
            file=sys.stderr,
            flush=True,
        )


def _bootstrap_webui_tunnel_for_netns(ns: str) -> None:
    """Infer management URL like isolate(auto) and start proxy when netns exists but state file missed it."""
    if os.geteuid() != 0 or not shutil.which("socat"):
        return
    if ns in _WEBUI_PROXY_STATE:
        return
    ns_ifaces = list_ns_ifaces(ns)
    if not ns_ifaces:
        return
    chosen = ""
    for iface in ns_ifaces:
        is_ww = iface.startswith("wwan")
        diag = collect_iface_diag(
            iface,
            iface_type="wwan" if is_ww else "usb",
            realtek_router_hint=False,
            netns=ns,
            probe_target=probe_config.DEFAULT_ROUTE_PROBE,
        )
        chosen = pick_management_host(diag)
        if chosen:
            break
    if not chosen:
        chosen = "192.168.1.1"
    boot_logs: List[str] = []
    mgmt_port, mgmt_https = auto_detect_mgmt_web(ns, chosen, boot_logs)
    record_ns_tunnel(ns, chosen, mgmt_port, management_https=mgmt_https)
    lp = start_webui_tunnel(ns, chosen, mgmt_port)
    if lp:
        record_ns_tunnel(ns, chosen, mgmt_port, listen_port=lp, management_https=mgmt_https)
        print(
            f"[5g-probe] Web UI tunnel auto-started for {ns} (no persistence row): "
            f"{'https' if mgmt_https else 'http'}://127.0.0.1:{lp}",
            file=sys.stderr,
            flush=True,
        )


def restore_webui_tunnels_on_startup() -> None:
    """Reload tunnel targets from disk and restart proxies; bootstrap stray ``ue*`` netns."""
    if os.geteuid() != 0:
        return
    live = list_netns()
    live_set = set(live)
    disk = _load_webui_tunnel_state_file()
    pruned = {k: v for k, v in disk.items() if k in live_set and isinstance(v, dict)}
    _NS_TUNNEL_CFG.clear()
    _NS_TUNNEL_CFG.update(pruned)
    _save_webui_tunnel_state_file()

    for ns, cfg in list(_NS_TUNNEL_CFG.items()):
        _ensure_webui_tunnel_from_cfg(ns, cfg)

    for ns in sorted(live_set):
        if not _UE_NS_NAME_RE.match(ns):
            continue
        if ns in _WEBUI_PROXY_STATE:
            continue
        _bootstrap_webui_tunnel_for_netns(ns)


def _tcp_probe_local(port: int, timeout_s: float = 0.4) -> bool:
    try:
        socket.create_connection(("127.0.0.1", int(port)), timeout=timeout_s).close()
        return True
    except OSError:
        return False


def _tcp_probe_via_netns(ns: str, host: str, port: int, timeout_s: float = 2.5) -> bool:
    """True if TCP handshake succeeds from inside the namespace (modem Web UI reachable)."""
    code = (
        "import socket;"
        f"s=socket.create_connection(({repr(host)}, {int(port)}), timeout={timeout_s});"
        "s.close()"
    )
    try:
        proc = subprocess.run(
            ["ip", "netns", "exec", ns, sys.executable, "-c", code],
            capture_output=True,
            timeout=timeout_s + 2.0,
            text=True,
            check=False,
        )
        return proc.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _http_probe_via_netns(
    ns: str,
    host: str,
    port: int,
    timeout_s: float = 3.0,
    *,
    strict_http: bool = False,
) -> Tuple[bool, str]:
    """Send a minimal HTTP/1.0 GET from inside netns. TCP can succeed while HTTP returns nothing (browser ERR_EMPTY_RESPONSE)."""
    h = repr(host)
    one = (
        f"import socket,sys;h={h};p={int(port)};t={float(timeout_s)};"
        "s=socket.create_connection((h,p),timeout=t);s.settimeout(t);"
        "s.sendall(b'GET / HTTP/1.0\\r\\nHost: '+h.encode('utf-8',errors='ignore')+b'\\r\\n\\r\\n');"
        "d=s.recv(8192);s.close();"
        "sys.exit(3 if not d else ("
        "4 if d[:1]==b'\\x16' else ("
        "0 if d.lstrip()[:4]==b'HTTP' or d.lstrip()[:1]==b'<' else 2)))"
    )
    try:
        proc = subprocess.run(
            ["ip", "netns", "exec", ns, sys.executable, "-c", one],
            capture_output=True,
            timeout=timeout_s + 3.0,
            text=True,
            check=False,
        )
        rc = proc.returncode
        if strict_http:
            if rc == 0:
                return True, ""
            if rc == 3:
                return False, "no bytes"
            if rc == 4:
                return False, "TLS first byte"
            if rc == 2:
                return False, "not HTTP/HTML"
            return False, f"unexpected rc={rc}"
        if rc == 0:
            return True, ""
        if rc == 3:
            return False, "HTTP probe got no bytes — browser ERR_EMPTY_RESPONSE likely (wrong port/protocol)."
        if rc == 4:
            return False, "Port speaks TLS first — try HTTPS / another port (plain socat tunnel won't serve HTTPS in browser)."
        if rc == 2:
            return True, ""
        return False, "HTTP probe failed."
    except subprocess.TimeoutExpired:
        return False, "HTTP probe timed out."
    except (FileNotFoundError, OSError) as exc:
        return False, f"HTTP probe error: {exc}"


def _mgmt_auto_dbg(logs: Optional[List[str]], msg: str) -> None:
    """Echo management auto-detect steps to isolate logs and to the server terminal (stderr)."""
    line = f"DEBUG management auto: {msg}"
    if logs is not None:
        logs.append(line)
    print(f"[5g-probe] {line}", file=sys.stderr, flush=True)


def auto_detect_mgmt_web(
    ns: str, host: str, dbg: Optional[List[str]] = None
) -> Tuple[int, bool]:
    """Pick management TCP port and whether the browser should use https:// on the localhost tunnel.

    Prefer :443 + TLS when an HTTPS GET succeeds — many routers (e.g. Teltonika) still accept TCP
    on :80 with a tiny non-HTTP payload, which previously fooled cleartext-only detection.
    """
    open80 = _tcp_probe_via_netns(ns, host, 80)
    open443 = _tcp_probe_via_netns(ns, host, 443)
    _mgmt_auto_dbg(dbg, f"TCP {host}:80 open={open80}, :443 open={open443}")

    if open443:
        https_ok, https_msg = _https_probe_via_netns(ns, host, 443)
        _mgmt_auto_dbg(
            dbg,
            f"HTTPS GET {host}:443 → ok={https_ok}"
            + (f" ({https_msg})" if https_msg else ""),
        )
        if https_ok:
            _mgmt_auto_dbg(dbg, "decision → 443 HTTPS (TLS GET succeeded)")
            return 443, True

    if open443:
        ok443_plain, det443 = _http_probe_via_netns(ns, host, 443, strict_http=False)
        _mgmt_auto_dbg(
            dbg,
            f"cleartext HTTP GET {host}:443 → ok={ok443_plain} ({det443})",
        )
        if not ok443_plain:
            _mgmt_auto_dbg(
                dbg,
                "decision → 443 HTTPS (no usable cleartext HTTP; expect TLS-only Web UI)",
            )
            return 443, True
        _mgmt_auto_dbg(dbg, "decision → 443 HTTP (unusual: plain HTTP answers on 443)")
        return 443, False

    if open80:
        ok80_strict, det80s = _http_probe_via_netns(ns, host, 80, strict_http=True)
        _mgmt_auto_dbg(
            dbg,
            f"HTTP GET {host}:80 strict → ok={ok80_strict} ({det80s})",
        )
        if ok80_strict:
            _mgmt_auto_dbg(dbg, "decision → 80 HTTP (valid HTTP/HTML)")
            return 80, False
        ok80_loose, det80l = _http_probe_via_netns(ns, host, 80, strict_http=False)
        _mgmt_auto_dbg(
            dbg,
            f"HTTP GET {host}:80 loose → ok={ok80_loose} ({det80l})",
        )
        if ok80_loose:
            _mgmt_auto_dbg(
                dbg,
                "decision → 80 HTTP (strict failed but loose OK — try HTTPS manual if UI broken)",
            )
            return 80, False

    _mgmt_auto_dbg(dbg, "decision → fallback 80 HTTP (no open port hint)")
    return 80, False


def _https_probe_via_netns(ns: str, host: str, port: int, timeout_s: float = 5.0) -> Tuple[bool, str]:
    """Minimal HTTPS GET from inside netns (TLS); verification disabled for self-signed LAN UI."""
    one = (
        "import ssl,socket,sys;"
        f"h={repr(host)};p={int(port)};t={float(timeout_s)};"
        "ctx=ssl.create_default_context();ctx.check_hostname=False;ctx.verify_mode=ssl.CERT_NONE;"
        "raw=socket.create_connection((h,p),timeout=t);"
        "s=ctx.wrap_socket(raw,server_hostname=h);"
        r"s.sendall(b'GET / HTTP/1.1\r\nHost: '+h.encode('utf-8',errors='ignore')+b'\r\nConnection: close\r\n\r\n');"
        "d=s.recv(16384);s.close();sys.exit(3 if not d else 0)"
    )
    try:
        proc = subprocess.run(
            ["ip", "netns", "exec", ns, sys.executable, "-c", one],
            capture_output=True,
            timeout=timeout_s + 4.0,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            return True, ""
        if proc.returncode == 3:
            return False, "HTTPS probe got no bytes."
        return False, "HTTPS probe failed."
    except subprocess.TimeoutExpired:
        return False, "HTTPS probe timed out."
    except (FileNotFoundError, OSError) as exc:
        return False, f"HTTPS probe error: {exc}"


def _https_probe_localhost_tunnel(
    listen_port: int,
    modem_host: str,
    timeout_s: float = 15.0,
) -> Tuple[bool, str]:
    """GET / through TLS to localhost tunnel (browser-like Host); cert checks off."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        raw = socket.create_connection(("127.0.0.1", int(listen_port)), timeout=timeout_s)
        raw.settimeout(timeout_s)
        sni = modem_host.strip() or None
        sock = ctx.wrap_socket(raw, server_hostname=sni)
        blob = (
            f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{listen_port}\r\nConnection: close\r\n\r\n"
        ).encode("ascii", errors="ignore")
        sock.settimeout(timeout_s)
        sock.sendall(blob)
        d = sock.recv(65536)
        sock.close()
        if not d:
            return False, "via localhost tunnel (HTTPS): empty response"
        dd = d.lstrip()
        if dd[:4] == b"HTTP" or dd[:1] == b"<":
            return True, ""
        return True, ""
    except ssl.SSLError as exc:
        return False, f"via localhost tunnel (HTTPS): TLS error {exc}"
    except OSError as exc:
        return False, f"via localhost tunnel (HTTPS): {exc}"


def _proxy_netns_socat_popen(ns: str, modem_host: str, modem_port: int) -> subprocess.Popen:
    """Run socat inside netns: stdio ↔ modem TCP (TLS passthrough).

    ``nodelay`` sets TCP_NODELAY on the leg toward the modem — without it, small TLS
    segments can sit in the Nagle buffer and the handshake appears to hang (timeouts
    only mask that). Unbuffered pipes + optional ``stdbuf`` avoid extra delay on stdio.
    """
    inner = ["socat", "-", f"TCP4:{modem_host}:{modem_port},nodelay"]
    sb = shutil.which("stdbuf")
    if sb:
        inner = [sb, "-o0", "-i0"] + inner
    return subprocess.Popen(
        ["ip", "netns", "exec", ns] + inner,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )


def _modem_host_header_bytes(modem_host: str, modem_port: int) -> bytes:
    if modem_port == 80:
        val = modem_host.encode("ascii", errors="ignore")
    else:
        val = f"{modem_host}:{modem_port}".encode("ascii", errors="ignore")
    return b"Host: " + val + b"\r\n"


def _webui_proxy_dispatch(client: socket.socket, ns: str, modem_host: str, modem_port: int) -> None:
    """Bridge HTTP (Host rewrite) or raw TLS bytes (HTTPS passthrough) to modem inside netns.

    TLS ClientHello records do not contain ``\\r\\n\\r\\n``; waiting for HTTP-style headers would
    deadlock the handshake (browser/probe sees SSL timeout).
    """
    try:
        first = client.recv(65536)
        if not first:
            return
        if probe_config.DEBUG_WEBUI_TUNNEL:
            now = time.monotonic()
            emit = False
            with _tunnel_proxy_dbg_lock:
                prev = _tunnel_proxy_dbg_last_mono.get(ns, 0.0)
                if now - prev >= WEBUI_TUNNEL_DEBUG_INTERVAL_S:
                    _tunnel_proxy_dbg_last_mono[ns] = now
                    emit = True
            if emit:
                print(
                    f"[5g-probe] webui-proxy {ns} rx len={len(first)} "
                    f"first8={first[:8].hex()}",
                    file=sys.stderr,
                    flush=True,
                )
        # TLS record layer: handshake records use content type 22 (0x16).
        if first[:1] == b"\x16":
            _proxy_run_upstream(client, ns, modem_host, modem_port, first)
            return
        buf = first
        while b"\r\n\r\n" not in buf and len(buf) < 65536:
            try:
                chunk = client.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
        if not buf:
            return
        first_line = buf.split(b"\r\n", 1)[0]
        if b"\r\n\r\n" in buf and _HTTP_REQ_LINE_START.match(first_line):
            _proxy_rewrite_host_http(client, ns, modem_host, modem_port, buf)
        else:
            _proxy_run_upstream(client, ns, modem_host, modem_port, buf)
    finally:
        try:
            client.close()
        except OSError:
            pass


def _shutdown_client_conn(client: socket.socket) -> None:
    """Stop browser wait: our reader thread blocks on modem keep-alive unless we shut down."""
    try:
        client.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _proxy_run_upstream(
    client: socket.socket,
    ns: str,
    modem_host: str,
    modem_port: int,
    initial: bytes,
) -> None:
    proc = _proxy_netns_socat_popen(ns, modem_host, modem_port)
    if not proc.stdin or not proc.stdout:
        _shutdown_client_conn(client)
        return
    try:
        proc.stdin.write(initial)
        proc.stdin.flush()
    except BrokenPipeError:
        proc.kill()
        _shutdown_client_conn(client)
        return

    def client_to_modem() -> None:
        try:
            while True:
                data = client.recv(65536)
                if not data:
                    break
                proc.stdin.write(data)
                proc.stdin.flush()
        except OSError:
            pass
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass

    def modem_to_client() -> None:
        try:
            while True:
                data = proc.stdout.read(65536)
                if not data:
                    break
                client.sendall(data)
        except OSError:
            pass
        finally:
            _shutdown_client_conn(client)

    rt = threading.Thread(target=modem_to_client, daemon=True)
    rt.start()
    client_to_modem()
    try:
        proc.wait(timeout=25)
    except subprocess.TimeoutExpired:
        proc.kill()
    finally:
        _shutdown_client_conn(client)


def _proxy_rewrite_host_http(
    client: socket.socket,
    ns: str,
    modem_host: str,
    modem_port: int,
    buf: bytes,
) -> None:
    head, sep, tail = buf.partition(b"\r\n\r\n")
    if not sep:
        _proxy_run_upstream(client, ns, modem_host, modem_port, buf)
        return
    head = _CONN_HEADER_LINE_RE.sub(b"", head)
    repl = _modem_host_header_bytes(modem_host, modem_port)
    new_head = _HOST_HEADER_LINE_RE.sub(repl, head)
    if new_head == head:
        new_head = head + b"\r\n" + repl
    new_head = new_head.rstrip(b"\r\n") + b"\r\nConnection: close\r\n"
    first = new_head + b"\r\n\r\n" + tail
    _proxy_run_upstream(client, ns, modem_host, modem_port, first)


def _webui_proxy_listen_loop(
    srv: socket.socket,
    stop_evt: threading.Event,
    ns: str,
    modem_host: str,
    modem_port: int,
) -> None:
    while not stop_evt.is_set():
        try:
            conn, _ = srv.accept()
            try:
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
        except socket.timeout:
            continue
        except OSError:
            break
        threading.Thread(
            target=_webui_proxy_dispatch,
            args=(conn, ns, modem_host, modem_port),
            daemon=True,
        ).start()


def _http_probe_localhost_tunnel(listen_port: int, timeout_s: float = 5.0) -> Tuple[bool, str]:
    """GET via localhost tunnel using browser-like Host header (must survive Host rewrite)."""
    blob = (
        f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{listen_port}\r\nConnection: close\r\n\r\n"
    ).encode("ascii", errors="ignore")
    try:
        s = socket.create_connection(("127.0.0.1", int(listen_port)), timeout=timeout_s)
        s.settimeout(timeout_s)
        s.sendall(blob)
        d = s.recv(32768)
        s.close()
        if not d:
            return False, "via localhost tunnel: empty response"
        if d[:1] == b"\x16":
            return False, "via localhost tunnel: TLS payload — try HTTPS management_port"
        dd = d.lstrip()
        if dd[:4] == b"HTTP" or dd[:1] == b"<":
            return True, ""
        return True, ""
    except OSError as exc:
        return False, f"via localhost tunnel: {exc}"


def _sanitize_mgmt_host(host: str) -> str:
    h = host.strip()
    if not h or not re.match(r"^[\w.-]+$", h):
        raise ValueError("invalid management host")
    return h


def start_webui_tunnel(ns: str, host: str = "192.168.1.1", remote_port: int = 80) -> Optional[int]:
    """Listen on localhost and forward HTTP toward the modem inside netns.

    Rewrites the HTTP ``Host`` header to the modem address — browsers send ``127.0.0.1:<port>``,
    which many dongles reject with an empty reply while raw TCP probes still succeed.
    """
    if not shutil.which("socat"):
        return None
    stop_webui_tunnel(ns)
    listen_port = webui_port_for_namespace(ns)
    modem_host = _sanitize_mgmt_host(host)
    modem_port = int(remote_port)

    stop_evt = threading.Event()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("127.0.0.1", listen_port))
    except OSError:
        srv.close()
        return None
    srv.listen(64)
    srv.settimeout(0.5)

    th = threading.Thread(
        target=_webui_proxy_listen_loop,
        args=(srv, stop_evt, ns, modem_host, modem_port),
        name=f"webui-proxy-{ns}",
        daemon=True,
    )
    th.start()
    _WEBUI_PROXY_STATE[ns] = {"srv": srv, "thread": th, "stop": stop_evt}
    return listen_port


def stop_webui_tunnel(ns: str) -> None:
    st = _WEBUI_PROXY_STATE.pop(ns, None)
    if st:
        st["stop"].set()
        try:
            st["srv"].close()
        except OSError:
            pass
        th = st.get("thread")
        if th is not None and th.is_alive():
            th.join(timeout=3.0)
    cfg = _NS_TUNNEL_CFG.get(ns) or {}
    lp = cfg.get("listen_port")
    if lp:
        fu = shutil.which("fuser")
        if fu:
            _run([fu, "-k", f"{int(lp)}/tcp"], check=False)
    _run(["pkill", "-f", f"netns exec {ns} socat"], check=False)


# ---------------------------------------------------------------------------
# REST routes (infrastructure)
# ---------------------------------------------------------------------------

def namespace_status_entry(ns: str) -> Dict[str, Any]:
    ns_ifaces = list_ns_ifaces(ns)
    listen_port = webui_port_for_namespace(ns)
    details: List[Dict[str, Any]] = []
    chosen_mgmt = ""
    for iface in ns_ifaces:
        is_ww = iface.startswith("wwan")
        diag = collect_iface_diag(
            iface,
            iface_type="wwan" if is_ww else "usb",
            realtek_router_hint=False,
            netns=ns,
            probe_target=probe_config.DEFAULT_ROUTE_PROBE,
        )
        details.append(diag)
        if not chosen_mgmt:
            chosen_mgmt = pick_management_host(diag)
    cfg = _NS_TUNNEL_CFG.get(ns) or {}
    mgmt_host = (cfg.get("management_host") or chosen_mgmt or "192.168.1.1").strip()
    try:
        mgmt_port = int(cfg.get("management_port") if cfg.get("management_port") is not None else 80)
    except (TypeError, ValueError):
        mgmt_port = 80
    mgmt_https = bool(cfg.get("management_https"))
    if not mgmt_https and mgmt_port == 443:
        mgmt_https = True

    tunnel_listen = _tcp_probe_local(listen_port)
    mgmt_tcp_ok = (
        _tcp_probe_via_netns(ns, mgmt_host, mgmt_port) if mgmt_host else False
    )
    modem_direct_http_ok = False
    modem_direct_detail = ""
    if mgmt_tcp_ok and mgmt_host:
        if mgmt_https:
            modem_direct_http_ok, modem_direct_detail = _https_probe_via_netns(
                ns, mgmt_host, mgmt_port
            )
        else:
            modem_direct_http_ok, modem_direct_detail = _http_probe_via_netns(
                ns, mgmt_host, mgmt_port
            )

    tunnel_http_ok = False
    tunnel_http_detail = ""
    if tunnel_listen:
        if mgmt_https:
            tunnel_http_ok, tunnel_http_detail = _https_probe_localhost_tunnel(
                listen_port, mgmt_host
            )
        else:
            tunnel_http_ok, tunnel_http_detail = _http_probe_localhost_tunnel(listen_port)

    mgmt_http_ok = tunnel_http_ok

    shell_cmd = f"sudo ip netns exec {ns} bash"
    tunnel_hint = None
    scheme = "https" if mgmt_https else "http"
    local_url = f"{scheme}://127.0.0.1:{listen_port}"
    if not tunnel_listen:
        tunnel_hint = "Tunnel port closed — Web UI proxy not listening; try Isolate again."
    elif not mgmt_tcp_ok:
        tunnel_hint = (
            f"No TCP from netns to {mgmt_host}:{mgmt_port} — Web UI may use another port "
            "(e.g. 8080) or HTTPS (443); set management_port / management_https when isolating."
        )
    elif not tunnel_http_ok:
        tunnel_hint = tunnel_http_detail or (
            "Probe via localhost tunnel failed — browser may stay loading or show a certificate warning."
        )
    elif not modem_direct_http_ok:
        tunnel_hint = (
            modem_direct_detail
            or "Direct modem Web UI probe from netns failed (unexpected if tunnel probe passed)."
        )

    return {
        "name": ns,
        "interfaces": ns_ifaces,
        "interface_details": details,
        "webui_port": listen_port,
        "management_selected": mgmt_host,
        "management_port": mgmt_port,
        "management_https": mgmt_https,
        "management_local_url": local_url,
        "webui_tunnel_listening": tunnel_listen,
        "management_tcp_ok": mgmt_tcp_ok,
        "management_http_ok": mgmt_http_ok,
        "webui_tunnel_hint": tunnel_hint,
        "shell_command": shell_cmd,
    }


@app.route("/")
def index():
    return render_template(
        "index.html",
        default_target_ip=probe_config.DEFAULT_UPF_CLOUD_IP,
        benchmark_targets=_merged_benchmark_targets(),
    )


@app.route("/api/config")
def api_config():
    user_bt = _load_user_benchmark_targets()
    return jsonify({
        "defaults": {"target_ip": probe_config.DEFAULT_UPF_CLOUD_IP},
        "benchmark_targets": list(probe_config.benchmark_targets()) + user_bt,
        "user_benchmark_targets": user_bt,
        "probe_root": probe_config.PACKAGE_ROOT,
        "results_root": RESULTS_DIR,
    })


@app.route("/api/user_benchmark_targets", methods=["POST"])
def api_add_user_benchmark_target():
    data = request.json or {}
    label = (data.get("label") or "").strip()
    tip = (data.get("ip") or "").strip()
    if not label:
        return jsonify({"ok": False, "error": "Label is required"}), 400
    if not _valid_benchmark_ip(tip):
        return jsonify({"ok": False, "error": "Invalid IP address"}), 400
    items = _load_user_benchmark_targets()
    if len(items) >= _USER_BENCHMARK_TARGET_CAP:
        return jsonify({"ok": False, "error": f"Too many saved presets (max {_USER_BENCHMARK_TARGET_CAP})"}), 400
    entry = {
        "id": f"user_{uuid.uuid4().hex[:12]}",
        "label": label[:120],
        "ip": tip,
    }
    items.append(entry)
    _save_user_benchmark_targets(items)
    merged = list(probe_config.benchmark_targets()) + items
    return jsonify({
        "ok": True,
        "target": entry,
        "benchmark_targets": merged,
        "user_benchmark_targets": items,
    })


@app.route("/api/user_benchmark_targets/<target_id>", methods=["DELETE"])
def api_delete_user_benchmark_target(target_id: str):
    tid = (target_id or "").strip()
    if not tid.startswith("user_"):
        return jsonify({"ok": False, "error": "Not a user-defined preset"}), 400
    items = _load_user_benchmark_targets()
    new_items = [x for x in items if x.get("id") != tid]
    if len(new_items) == len(items):
        return jsonify({"ok": False, "error": "Preset not found"}), 404
    _save_user_benchmark_targets(new_items)
    merged = list(probe_config.benchmark_targets()) + new_items
    return jsonify({"ok": True, "benchmark_targets": merged, "user_benchmark_targets": new_items})


@app.route("/api/status")
def api_status():
    ifaces = list_usb_ifaces()
    namespaces = list_netns()
    ns_details = [namespace_status_entry(ns) for ns in namespaces]
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
            # dhclient normally installs default via DHCP gateway (classless static route option).
            # Adding default dev <iface> again causes "RTNETLINK answers: File exists" and adds noise only.
            default_rt = _run_capture(["ip", "-n", ns, "route", "show", "default"])
            if not default_rt.strip():
                _run(["ip", "netns", "exec", ns, "ip", "route", "add", "default", "dev", iface], check=False)
                logs.append(f"Default route via '{iface}' (DHCP left no default).")

        diag = collect_iface_diag(
            iface,
            iface_type="wwan" if is_wwan else "usb",
            realtek_router_hint=False,
            netns=ns,
            probe_target=probe_config.DEFAULT_ROUTE_PROBE,
        )

        mgmt_host_raw = (data.get("management_host") or "").strip()
        if mgmt_host_raw:
            try:
                mgmt_host = _sanitize_mgmt_host(mgmt_host_raw)
            except ValueError:
                return jsonify({"status": "error", "message": "Invalid management_host.", "data": {"logs": logs}}), 400
        else:
            mgmt_host = pick_management_host(diag)

        mp_raw = data.get("management_port")
        mh_raw = data.get("management_https")
        user_set_port = mp_raw is not None and mp_raw != ""
        if user_set_port:
            try:
                mgmt_port = int(mp_raw)
            except (TypeError, ValueError):
                return jsonify(
                    {
                        "status": "error",
                        "message": "Invalid management_port.",
                        "data": {"logs": logs},
                    }
                ), 400
            if isinstance(mh_raw, bool):
                mgmt_https = mh_raw
            else:
                mgmt_https = mgmt_port == 443
            logs.append(
                f"Management UI (manual): {mgmt_host}:{mgmt_port} "
                f"({'HTTPS' if mgmt_https else 'HTTP'})"
            )
        else:
            mgmt_port, mgmt_https = auto_detect_mgmt_web(ns, mgmt_host, logs)
            logs.append(
                f"Management UI (auto): {mgmt_host}:{mgmt_port} "
                f"({'HTTPS' if mgmt_https else 'HTTP'})"
            )

        record_ns_tunnel(ns, mgmt_host, mgmt_port, management_https=mgmt_https)

        port = None
        sch = "https" if mgmt_https else "http"
        try:
            port = start_webui_tunnel(ns, mgmt_host, mgmt_port)
            if port:
                record_ns_tunnel(ns, mgmt_host, mgmt_port, listen_port=port)
                logs.append(
                    f"WebUI tunnel: {sch}://127.0.0.1:{port} → {mgmt_host}:{mgmt_port} "
                    f"({'TLS passthrough' if mgmt_https else 'HTTP + Host rewrite'})"
                )
                time.sleep(0.35)
                reach = _tcp_probe_via_netns(ns, mgmt_host, mgmt_port)
                listen_ok = _tcp_probe_local(port)
                if mgmt_https:
                    http_ok, http_msg = (
                        _https_probe_via_netns(ns, mgmt_host, mgmt_port)
                        if reach
                        else (False, "")
                    )
                else:
                    http_ok, http_msg = (
                        _http_probe_via_netns(ns, mgmt_host, mgmt_port)
                        if reach
                        else (False, "")
                    )
                if not listen_ok:
                    logs.append(
                        "WARNING: Localhost Web UI proxy did not accept TCP — port conflict or proxy failed to bind."
                    )
                if not reach:
                    logs.append(
                        f"WARNING: From netns, TCP to management {mgmt_host}:{mgmt_port} failed — "
                        "check management_host/port or firewall on the device."
                    )
                elif not http_ok:
                    logs.append(
                        f"WARNING: {http_msg or 'Web UI probe from netns failed — wrong port or protocol.'}"
                    )
                browser_ok, browser_msg = (False, "")
                if listen_ok:
                    if mgmt_https:
                        browser_ok, browser_msg = _https_probe_localhost_tunnel(port, mgmt_host)
                    else:
                        browser_ok, browser_msg = _http_probe_localhost_tunnel(port)
                if listen_ok and browser_ok:
                    logs.append(
                        "WebUI OK: localhost tunnel probe passed ("
                        + ("HTTPS/TLS" if mgmt_https else "HTTP Host rewrite")
                        + ")."
                    )
                elif listen_ok and reach and http_ok and not browser_ok:
                    logs.append(f"WARNING: Browser-like tunnel probe failed: {browser_msg}")
        except ValueError as ve:
            logs.append(f"WebUI tunnel skipped: {ve}")

        shell_cmd = f"sudo ip netns exec {ns} bash"
        return jsonify({"status": "success", "message": f"'{iface}' isolated → '{ns}'.",
                        "data": {"namespace": ns, "interface": iface, "webui_port": port,
                                 "management_host": mgmt_host, "management_port": mgmt_port,
                                 "management_https": mgmt_https,
                                 "management_local_url": f"{sch}://127.0.0.1:{port}" if port else None,
                                 "logs": logs, "iface_type": "wwan" if is_wwan else "usb",
                                 "restored_ip": saved_ip, "diagnostics": diag,
                                 "shell_command": shell_cmd}})
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
        logs.append("WebUI tunnel stop attempted.")

        if ns not in list_netns():
            clear_ns_tunnel(ns)
            logs.append(f"Namespace '{ns}' does not exist — nothing to tear down (UI may be stale).")
            _run(["pkill", "-f", "dhclient"], check=False)
            logs.append("Cleanup done.")
            return jsonify(
                {
                    "status": "success",
                    "message": f"'{ns}' was already absent.",
                    "data": {"logs": logs},
                }
            )

        for iface in list_ns_ifaces(ns):
            _run(["ip", "-n", ns, "link", "set", iface, "netns", "1"], check=False)
            _run(["ip", "link", "set", iface, "up"], check=False)
            logs.append(f"'{iface}' → host.")

        _run(["ip", "netns", "delete", ns], check=False)
        logs.append(f"Namespace '{ns}' deleted.")
        clear_ns_tunnel(ns)
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
    target_ip = data.get("target_ip", probe_config.DEFAULT_UPF_CLOUD_IP)
    results: Dict[str, Any] = {"timestamp": int(time.time()), "target": target_ip}

    try:
        parallel_streams = max(1, min(int(data.get("parallel_streams", 1)), 64))
        ps_flag = f" -P {parallel_streams}" if parallel_streams > 1 else ""

        ping_out = _run_shell(f"ip netns exec {ns} ping -c 5 -q {target_ip}")
        try:
            avg_ping = float(ping_out.split("=")[1].split("/")[1])
            results["ping_idle_ms"] = round(avg_ping, 2)
        except Exception:
            results["ping_idle_ms"] = -1

        def _iperf_quick(cmd: str, key: str) -> Tuple[float, Optional[str]]:
            try:
                out = _run_shell(cmd)
                data = json.loads(out) if out else {}
            except Exception as exc:
                return 0.0, f"iperf3 invocation failed: {exc}"
            err = data.get("error")
            if err:
                return 0.0, str(err)
            bps = data.get("end", {}).get(key, {}).get("bits_per_second", 0)
            return round((bps or 0) / 1e6, 2), None

        dl_mbps, dl_err = _iperf_quick(
            f"ip netns exec {ns} iperf3 -c {target_ip} -R -t 5 -J{ps_flag}",
            "sum_received",
        )
        results["dl_mbps"] = dl_mbps
        if dl_err:
            results["dl_error"] = dl_err

        ul_mbps, ul_err = _iperf_quick(
            f"ip netns exec {ns} iperf3 -c {target_ip} -t 5 -J{ps_flag}",
            "sum_sent",
        )
        results["ul_mbps"] = ul_mbps
        if ul_err:
            results["ul_error"] = ul_err

        if dl_err and ul_err:
            results["status"] = "error"
            results["message"] = f"iperf3 unreachable on {target_ip}:5201 — {dl_err}"
        else:
            results["status"] = "success"
            if dl_err or ul_err:
                results["message"] = "Partial: " + (dl_err or ul_err)
    except Exception as exc:
        results["status"] = "error"
        results["message"] = str(exc)
    return jsonify(results)


# ---------------------------------------------------------------------------
# Module-level iperf3 phase runner
# ---------------------------------------------------------------------------

def _effective_udp_bandwidth(session: BenchmarkSession, phase_mode: str) -> str:
    if session.proto != "udp":
        return session.bandwidth
    if phase_mode == "dl" and session.bandwidth_dl:
        return session.bandwidth_dl
    return session.bandwidth


def _udp_length_args(session: BenchmarkSession) -> List[str]:
    if session.proto != "udp":
        return []
    mode = (session.udp_length_mode or "fixed").lower()
    if mode == "omit":
        return []
    if mode == "auto":
        out = _run_capture(["ip", "-n", session.ns, "route", "get", session.target_ip])
        dev, rmtu = net_diag.parse_route_get_dev_mtu(out)
        link_mtu = 1500
        if dev:
            link_out = _run_capture(["ip", "-n", session.ns, "link", "show", "dev", dev])
            link_mtu = net_diag.parse_link_mtu(link_out)
        lb = net_diag.udp_payload_from_path_mtu(rmtu, link_mtu, session.udp_mtu_clamp)
        return ["-l", str(lb)]
    return ["-l", str(session.length_bytes)]


def run_iperf_phase(session: BenchmarkSession, phase_mode: str, phase_duration: int) -> dict:
    """Run one iperf3 phase, emit iperf_data events, return full phase summary dict.

    The summary includes:
      - client_intervals: list of per-interval dicts (client_mbps + server_mbps backfilled
        from server_intervals when available)
      - server_intervals: list of per-interval dicts from --get-server-output block
      - client_mbps, server_mbps, loss_pct, jitter_ms, total_retr: final summary values
    """
    reverse_flag = ["-R"] if phase_mode == "dl" else []
    bw_eff = _effective_udp_bandwidth(session, phase_mode)
    udp_flags = ["-u", "-b", bw_eff] if session.proto == "udp" else []
    length_flag = _udp_length_args(session)

    interval_s = max(0.05, float(session.interval_s))
    par_flag: List[str] = []
    if session.parallel_streams > 1:
        par_flag = ["-P", str(session.parallel_streams)]

    iperf_cmd = [
        "ip", "netns", "exec", session.ns,
        "iperf3", "-c", session.target_ip,
        "-t", str(phase_duration), "-i", str(interval_s),
        "-f", "m", "--forceflush", "--get-server-output",
    ] + par_flag + udp_flags + length_flag + reverse_flag

    udp_payload_for_csv: Optional[int] = None
    if session.proto == "udp":
        udp_payload_for_csv = int(length_flag[1]) if len(length_flag) >= 2 else None

    # UDP UL: client is the sender → interval stats are injected traffic, NOT received
    is_sender_side = session.proto == "udp" and phase_mode != "dl"

    proto_label = f"UDP ({bw_eff})" if session.proto == "udp" else "TCP"
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
            # With parallel streams iperf3 emits per-stream summaries then [SUM]; skip per-stream.
            if session.parallel_streams > 1 and "[SUM]" not in line:
                continue
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

        # With parallel streams iperf3 emits one line per stream then [SUM]; skip per-stream.
        if session.parallel_streams > 1 and "[SUM]" not in line:
            continue

        if in_server_output:
            # Parse the server-side interval: capture time bounds + UDP/TCP stats
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
            elif session.proto == "tcp":
                # For TCP DL the server is the sender — parse retr + cwnd from server output.
                tokens = line.split()
                try:
                    mbps_idx = next(i for i, t in enumerate(tokens) if t == "Mbits/sec")
                    if mbps_idx + 3 < len(tokens):
                        retr = int(tokens[mbps_idx + 1])
                        cwnd_val = float(tokens[mbps_idx + 2])
                        unit = tokens[mbps_idx + 3]
                        cwnd_kb = cwnd_val * 1024 if unit == "MBytes" else cwnd_val
                        srv["retr"] = retr
                        srv["cwnd_kb"] = cwnd_kb
                except (StopIteration, ValueError, IndexError):
                    pass
            phase_summary["server_intervals"].append(srv)

        else:
            # Client-side interval — emit live, accumulate for CSV
            iv = max(0.05, float(session.interval_s))
            tm_ci = re.search(r"([\d.]+)-([\d.]+)\s+sec", line)
            if tm_ci:
                i_sta, i_en = float(tm_ci.group(1)), float(tm_ci.group(2))
                if i_sta == i_en:
                    continue
                t_val = round(i_en, 3)
                session.second_counter["val"] = t_val
                interval_start = round(i_sta, 3)
                interval_end = round(i_en, 3)
            else:
                session.second_counter["val"] = round(session.second_counter["val"] + iv, 3)
                t_val = session.second_counter["val"]
                interval_start = round(t_val - iv, 3)
                interval_end = t_val

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
                "interval_start": interval_start,
                "interval_end": interval_end,
                "length_bytes": udp_payload_for_csv if session.proto == "udp" else None,
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

    # Back-fill server telemetry (mbps, loss, jitter, retr, cwnd) from server_intervals
    n = min(len(phase_summary["client_intervals"]), len(phase_summary["server_intervals"]))
    for i in range(n):
        srv = phase_summary["server_intervals"][i]
        ci = phase_summary["client_intervals"][i]
        ci["server_mbps"] = srv["mbps"]
        if "loss_pct" in srv:
            ci["loss_pct"] = srv["loss_pct"]
        if "jitter_ms" in srv:
            ci["jitter_ms"] = srv["jitter_ms"]
        # TCP DL: server is the sender — back-fill retransmits + cwnd
        if "retr" in srv:
            ci["retransmits"] = srv["retr"]
        if "cwnd_kb" in srv:
            ci["cwnd_kb"] = srv["cwnd_kb"]

    # Emit backfilled data so the frontend can update its tpData for CSV export
    if phase_summary["server_intervals"]:
        socketio.emit("iperf_backfill", {
            "phase": phase_mode,
            "intervals": [
                {
                    "loss_pct": ci.get("loss_pct"),
                    "jitter_ms": ci.get("jitter_ms"),
                    "retransmits": ci.get("retransmits"),
                    "cwnd_kb": ci.get("cwnd_kb"),
                }
                for ci in phase_summary["client_intervals"]
            ],
        }, to=session.sid)

    return phase_summary


# ---------------------------------------------------------------------------
# SocketIO — Live Benchmark
# ---------------------------------------------------------------------------

def _live_benchmark_thread(sid: str, ns: str, target_ip: str, duration: int, mode: str,
                           proto: str = "tcp", bandwidth: str = "200M",
                           bandwidth_dl: Optional[str] = None,
                           length_bytes: int = 1200,
                           parallel_streams: int = 1,
                           interval_s: float = 0.1,
                           udp_length_mode: str = "fixed",
                           udp_mtu_clamp: int = 1200,
                           with_ping: bool = True):
    """Background thread: runs iperf3 (optionally + ping) and streams data via SocketIO."""

    ping_cmd = ["ip", "netns", "exec", ns, "ping", target_ip]
    tests_done = threading.Event()
    ping_proc = None
    t_ping = None
    session = BenchmarkSession(
        sid=sid, ns=ns, target_ip=target_ip, proto=proto,
        bandwidth=bandwidth, bandwidth_dl=bandwidth_dl,
        length_bytes=length_bytes, parallel_streams=parallel_streams,
        interval_s=interval_s, udp_length_mode=udp_length_mode,
        udp_mtu_clamp=udp_mtu_clamp,
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
    target_ip = data.get("target_ip", probe_config.DEFAULT_UPF_CLOUD_IP)
    duration = min(int(data.get("duration", 10)), 120)
    mode = data.get("mode", "dl")
    proto = data.get("proto", "tcp")
    bandwidth = data.get("bandwidth", "200M")
    bw_dl_raw = data.get("bandwidth_dl")
    bandwidth_dl: Optional[str] = None
    if bw_dl_raw is not None and str(bw_dl_raw).strip() != "":
        if isinstance(bw_dl_raw, (int, float)):
            bandwidth_dl = f"{int(bw_dl_raw)}M"
        else:
            bandwidth_dl = str(bw_dl_raw).strip()

    length_bytes = max(68, min(int(data.get("length_bytes", 1200)), 65507))
    parallel_streams = max(1, min(int(data.get("parallel_streams", 1)), 64))
    interval_s = max(0.05, float(data.get("interval_s", 0.1)))
    udp_length_mode = (data.get("udp_length_mode") or "fixed").lower()
    if udp_length_mode not in ("omit", "auto", "fixed"):
        udp_length_mode = "fixed"
    udp_mtu_clamp = max(576, min(int(data.get("udp_mtu_clamp", 1200)), 9000))
    with_ping = bool(data.get("with_ping", False))

    with _active_lock:
        for p in _active_procs.get(sid, []):
            if p and p.poll() is None:
                p.kill()
        _active_procs.pop(sid, None)

    t = threading.Thread(
        target=_live_benchmark_thread,
        kwargs={
            "sid": sid,
            "ns": ns,
            "target_ip": target_ip,
            "duration": duration,
            "mode": mode,
            "proto": proto,
            "bandwidth": bandwidth,
            "bandwidth_dl": bandwidth_dl,
            "length_bytes": length_bytes,
            "parallel_streams": parallel_streams,
            "interval_s": interval_s,
            "udp_length_mode": udp_length_mode,
            "udp_mtu_clamp": udp_mtu_clamp,
            "with_ping": with_ping,
        },
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
    target_ip = data.get("target_ip", probe_config.DEFAULT_UPF_CLOUD_IP)
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
    _chown_result(path)


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
    _chown_result(path)


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
    _chown_result(filepath)


# ---------------------------------------------------------------------------
# Test Results — save & browse
# ---------------------------------------------------------------------------

def _make_result_dir(result_path_hint: Optional[str],
                     test_type: str, direction: str, protocol: str) -> str:
    """Create and return the result subdirectory path."""
    if result_path_hint and (
        result_path_hint.startswith("plans/") or result_path_hint.startswith("plan_runs/")
    ):
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
    _makedirs_chown(result_dir)
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
            chart_path = os.path.join(result_dir, "chart.png")
            with open(chart_path, "wb") as f:
                f.write(base64.b64decode(img_data))
            _chown_result(chart_path)

        # Save data.csv
        csv_written = False
        if intervals:
            csv_path = os.path.join(result_dir, "data.csv")
            with open(csv_path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=_IPERF_CSV_FIELDS)
                w.writeheader()
                for idx, row in enumerate(intervals, 1):
                    w.writerow(_normalise_iperf_row(row, idx))
            _chown_result(csv_path)
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
        meta_path = os.path.join(result_dir, "metadata.json")
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)
        _chown_result(meta_path)

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
        "remotes": [],
    }

    # --- Remote-imported bundles: results/remotes/{folder}/metadata.json ---
    remotes_dir = os.path.join(RESULTS_DIR, "remotes")
    if os.path.isdir(remotes_dir):
        for entry in sorted(os.listdir(remotes_dir), reverse=True):
            entry_path = os.path.join(remotes_dir, entry)
            if not os.path.isdir(entry_path):
                continue
            meta_path = os.path.join(entry_path, "metadata.json")
            if not os.path.exists(meta_path):
                continue
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                meta["result_path"] = f"remotes/{entry}"
                meta["_remote"] = True
                result["remotes"].append(meta)
            except Exception:
                pass

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
        if entry in ("standalone", "plans", "plan_runs", "remotes"):
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

    # --- Plans: templates in ``plan_templates``; latest run overlay from ``results/plan_runs`` ---
    for plan_name in _iter_plan_template_slugs():
        plan_json = _plan_template_json(plan_name)
        if not os.path.exists(plan_json):
            continue
        try:
            plan_data = _plan_overlay_latest_run(plan_name)
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
        else:
            return jsonify({"ok": False, "error": "Not found"}), 404
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

        env = _session_desktop_env()
        wrap: List[str] = []
        sudo_user = os.environ.get("SUDO_USER")
        if os.geteuid() == 0 and sudo_user and sudo_user != "root":
            wrap = ["runuser", "-u", sudo_user, "--"]

        # Fire-and-forget: do NOT wait — xdg-open may block for seconds
        subprocess.Popen(
            wrap + ["xdg-open", folder_path],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return jsonify({"ok": True, "path": folder_path})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "xdg-open not found"}), 500
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


def _parse_proc_environ_blob(blob: bytes) -> Dict[str, str]:
    env: Dict[str, str] = {}
    for raw in blob.split(b"\0"):
        if not raw or b"=" not in raw:
            continue
        key_b, _, val_b = raw.partition(b"=")
        key = key_b.decode(errors="ignore")
        if key:
            env[key] = val_b.decode(errors="surrogateescape")
    return env


def _load_run_user_environ(uid: int) -> Dict[str, str]:
    path = f"/run/user/{uid}/environ"
    try:
        with open(path, "rb") as f:
            return _parse_proc_environ_blob(f.read())
    except OSError:
        return {}


def _session_desktop_env() -> Dict[str, str]:
    """Merge GUI-related vars so root→runuser terminal/xdg-open reach the user's session."""
    env = os.environ.copy()
    merge_keys = (
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "XDG_SESSION_TYPE",
        "XDG_SESSION_DESKTOP",
        "XDG_CURRENT_DESKTOP",
        "DBUS_SESSION_BUS_ADDRESS",
        "XDG_RUNTIME_DIR",
        "PATH",
    )
    uid_s = os.environ.get("SUDO_UID")
    uid_i: Optional[int] = None
    if uid_s and uid_s.isdigit():
        uid_i = int(uid_s)
        merged = _load_run_user_environ(uid_i)
        for k in merge_keys:
            if (k not in env or env.get(k) in ("", None)) and k in merged:
                env[k] = merged[k]
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid_i}")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid_i}/bus")
    else:
        u = os.getuid()
        merged = _load_run_user_environ(u)
        for k in merge_keys:
            if (k not in env or env.get(k) in ("", None)) and k in merged:
                env[k] = merged[k]
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{u}")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{u}/bus")

    if uid_i is not None:
        xa = env.get("XAUTHORITY") or ""
        if not xa or not os.path.isfile(xa):
            try:
                home = pwd.getpwuid(uid_i).pw_dir
                cand = os.path.join(home, ".Xauthority")
                if os.path.isfile(cand):
                    env["XAUTHORITY"] = cand
            except KeyError:
                pass

    if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        env.setdefault("DISPLAY", ":0")

    env.setdefault(
        "PATH",
        "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    )
    return env


_XDG_TERMINAL_VERIFY_PATTERNS: Tuple[str, ...] = (
    "gnome-terminal",
    "gnome-terminal-server",
    "kgx",
    "ptyxis",
    "konsole",
    "xfce4-terminal",
    "tilix",
    "terminator",
    "mate-terminal",
    "lxterminal",
    "kitty",
    "alacritty",
    "foot",
)


def _pgrep_user(uid: int, pattern: str) -> bool:
    try:
        r = subprocess.run(
            ["pgrep", "-u", str(uid), "-f", pattern],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return r.returncode == 0 and bool(r.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _verify_graphical_terminal(uid: int, tm: List[str]) -> bool:
    """Confirm an emulator process for uid exists after spawn (parent often exits 0 immediately)."""
    primary = tm[0].split("/")[-1]
    time.sleep(1.05)
    if primary == "xdg-terminal-exec":
        return any(_pgrep_user(uid, p) for p in _XDG_TERMINAL_VERIFY_PATTERNS)
    if primary == "gnome-terminal":
        return _pgrep_user(uid, "gnome-terminal") or _pgrep_user(
            uid, "gnome-terminal-server"
        )
    return _pgrep_user(uid, primary)


# Emulators that often return EACCES when exec'd as root (e.g. GNOME Console).
_TERMINAL_SKIP_WHEN_ROOT = frozenset({"kgx", "ptyxis"})


def _terminal_launch_prefixes(sudo_user: str) -> List[Optional[List[str]]]:
    """Try the desktop user's emulator first (kgx, …); inner command uses ``sudo ip netns exec``.

    Fallback last: root + ``ip netns exec`` only (no inner sudo — avoids password but skips kgx).
    """
    if os.geteuid() != 0:
        return [None]
    prefixes: List[Optional[List[str]]] = []
    if sudo_user and sudo_user != "root":
        prefixes.extend(
            [
                ["runuser", "-u", sudo_user, "--"],
                ["sudo", "-n", "-u", sudo_user, "-E"],
            ]
        )
    prefixes.append(None)
    return prefixes


def _login_user_can_sudo_n_netns(ns: str, login_user: str) -> bool:
    """True if login_user may run ``sudo -n ip netns exec <ns> …`` without a prompt.

    Works with NOPASSWD rules, or when sudo is built / configured so a recent ``sudo -v``
    (e.g. from ./run-probe.sh) still authorises non-interactive sudo for that user.

    Note: many distros default to *tty tickets* — a prompt in another window (kgx) can still
    happen even after ``run-probe.sh``; ``Defaults:user !tty_tickets`` relaxes that (see sudoers(5)).
    """
    if os.geteuid() != 0 or not login_user or login_user == "root":
        return False
    inner = "sudo -n ip netns exec " + shlex.quote(ns) + " true"
    try:
        r = subprocess.run(
            ["runuser", "-u", login_user, "--", "bash", "-c", inner],
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def _spawn_gui_terminal(cmd: List[str], env: Dict[str, str]) -> Tuple[bool, str]:
    """Start a terminal emulator; return (ok, stderr/exit hint)."""
    try:
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError:
        return False, "not installed"
    except PermissionError as exc:
        # e.g. GNOME Console (kgx) may refuse exec as root — try next emulator / runuser prefix.
        return False, str(exc)
    time.sleep(0.45)
    rc = proc.poll()
    err_txt = ""
    if proc.stderr:
        try:
            err_raw = proc.stderr.read() or b""
            err_txt = err_raw.decode(errors="replace").strip()[:800]
        except Exception:
            pass
    if rc is None:
        return True, ""
    if rc == 0:
        if err_txt and any(
            x in err_txt.lower()
            for x in (
                "cannot open display",
                "cannot connect to x server",
                "no protocol specified",
                "authorization required",
                "error opening display",
                "failed to connect",
            )
        ):
            return False, err_txt
        return True, ""
    return False, err_txt or f"exit code {rc}"


@app.route("/api/ssh_hosts", methods=["GET"])
def ssh_hosts():
    """List Host blocks from the desktop user's ~/.ssh/config (for Remote UE suggestions)."""
    try:
        sudo_user = os.environ.get("SUDO_USER", "") or ""
        if sudo_user and sudo_user != "root":
            home = os.path.expanduser(f"~{sudo_user}")
        else:
            home = os.path.expanduser("~")
        cfg = os.path.join(home, ".ssh", "config")
        hosts: List[Dict[str, Any]] = []
        if not os.path.isfile(cfg):
            return jsonify({"hosts": hosts, "config_path": cfg, "exists": False})
        current: Optional[Dict[str, Any]] = None
        with open(cfg) as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(None, 1)
                if len(parts) < 2:
                    continue
                key, val = parts[0].lower(), parts[1].strip()
                if key == "host":
                    # A "Host" line may declare multiple patterns; skip wildcards.
                    for name in val.split():
                        if any(c in name for c in "*?!"):
                            continue
                        current = {"host": name, "hostname": None, "user": None, "port": None}
                        hosts.append(current)
                    continue
                if current is None:
                    continue
                if key == "hostname":
                    current["hostname"] = val
                elif key == "user":
                    current["user"] = val
                elif key == "port":
                    current["port"] = val
        return jsonify({"hosts": hosts, "config_path": cfg, "exists": True})
    except Exception as exc:
        return jsonify({"hosts": [], "error": str(exc)}), 500


@app.route("/api/open_terminal", methods=["POST"])
def open_terminal():
    """Open a desktop terminal in a given working directory.

    Body: {"cwd": "<absolute path>"}  (defaults to the probe package root).
    Reuses the same emulator candidates / desktop-session env as open_netns_terminal.
    """
    try:
        data = request.get_json(silent=True) or {}
        cwd = str(data.get("cwd") or probe_config.PACKAGE_ROOT).strip()
        # Expand ~ relative to the desktop user (SUDO_USER) rather than root
        sudo_user_env = os.environ.get("SUDO_USER", "") or ""
        if cwd.startswith("~"):
            if sudo_user_env and sudo_user_env != "root":
                home = os.path.expanduser(f"~{sudo_user_env}")
                cwd = home + cwd[1:] if cwd != "~" else home
            else:
                cwd = os.path.expanduser(cwd)
        if not os.path.isdir(cwd):
            return jsonify({"ok": False, "error": f"Directory not found: {cwd}"}), 400

        env = _session_desktop_env()
        sudo_user = os.environ.get("SUDO_USER", "") or ""
        prefixes = _terminal_launch_prefixes(sudo_user)
        # `exec bash` keeps the window open after cd; -i ensures prompt + history.
        inner = f"cd {shlex.quote(cwd)} && exec bash -i"

        templates: List[List[str]] = []
        if shutil.which("xdg-terminal-exec"):
            templates.append(["xdg-terminal-exec", "--", "bash", "-c", inner])
        templates.extend([
            ["kgx", "--", "bash", "-c", inner],
            ["ptyxis", "--", "bash", "-c", inner],
            ["gnome-terminal", "--", "bash", "-c", inner],
            ["konsole", "-e", "bash", "-c", inner],
            ["xfce4-terminal", "-x", "bash", "-c", inner],
            ["tilix", "-e", "bash", "-c", inner],
            ["terminator", "-x", "bash", "-c", inner],
            ["mate-terminal", "-x", "bash", "-c", inner],
            ["lxterminal", "-e", "bash", "-c", inner],
            ["kitty", "bash", "-c", inner],
            ["alacritty", "-e", "bash", "-c", inner],
            ["foot", "bash", "-c", inner],
            ["x-terminal-emulator", "-e", "bash", "-c", inner],
            ["xterm", "-e", "bash", "-c", inner],
        ])

        last_err = "no terminal binary found"
        for prefix in prefixes:
            for tm in templates:
                term_bin = shutil.which(tm[0])
                if not term_bin:
                    continue
                base = os.path.basename(term_bin)
                if prefix is None and base in _TERMINAL_SKIP_WHEN_ROOT:
                    continue
                tm_use = [term_bin] + tm[1:]
                cmd = (prefix + tm_use) if prefix else tm_use
                ok, err = _spawn_gui_terminal(cmd, env)
                if ok:
                    return jsonify({"ok": True, "cwd": cwd, "terminal": base})
                if err and err != "not installed":
                    last_err = err
        return jsonify({"ok": False, "error": last_err}), 500
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/open_netns_terminal", methods=["POST"])
def open_netns_terminal():
    """Open a desktop terminal running an interactive shell inside the netns."""
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            data = {}
        ns = str(data.get("namespace", "") or "").strip()
        if not ns:
            return jsonify({"ok": False, "error": "Missing namespace"}), 400
        if ns not in list_netns():
            return jsonify({"ok": False, "error": "Namespace not found"}), 404

        shell_cmd = f"sudo ip netns exec {shlex.quote(ns)} bash"
        inner_as_root = f"ip netns exec {shlex.quote(ns)} bash -i"
        inner_as_login_nopass = f"sudo -n ip netns exec {shlex.quote(ns)} bash -i"
        inner_as_login_interactive = f"sudo ip netns exec {shlex.quote(ns)} bash -i"

        sudo_user = os.environ.get("SUDO_USER")

        env = _session_desktop_env()
        has_gui = bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))

        verify_user_uid: Optional[int] = None
        if sudo_user and sudo_user != "root":
            try:
                verify_user_uid = pwd.getpwnam(sudo_user).pw_uid
            except KeyError:
                verify_user_uid = None

        login_inner_nopass = (
            bool(sudo_user and sudo_user != "root")
            and _login_user_can_sudo_n_netns(ns, sudo_user)
        )

        prefixes = _terminal_launch_prefixes(sudo_user or "")
        last_err = "no terminal binary matched"
        for prefix in prefixes:
            if prefix and verify_user_uid is None:
                continue
            if prefix:
                inner = inner_as_login_nopass if login_inner_nopass else inner_as_login_interactive
            else:
                inner = inner_as_root
            templates: List[List[str]] = []
            if shutil.which("xdg-terminal-exec"):
                templates.append(["xdg-terminal-exec", "--", "bash", "-ilc", inner])
            # User prefixes try kgx first; root skips kgx/ptyxis → xterm only as last resort.
            templates.extend(
                [
                    ["kgx", "--", "bash", "-ilc", inner],
                    ["ptyxis", "--", "bash", "-ilc", inner],
                    ["gnome-terminal", "--", "bash", "-ilc", inner],
                    ["konsole", "-e", "bash", "-ilc", inner],
                    ["xfce4-terminal", "-x", "bash", "-ilc", inner],
                    ["tilix", "-e", "bash", "-ilc", inner],
                    ["terminator", "-x", "bash", "-ilc", inner],
                    ["mate-terminal", "-x", "bash", "-ilc", inner],
                    ["lxterminal", "-e", "bash", "-ilc", inner],
                    ["kitty", "bash", "-ilc", inner],
                    ["alacritty", "-e", "bash", "-ilc", inner],
                    ["foot", "bash", "-ilc", inner],
                    ["x-terminal-emulator", "-e", "bash", "-ilc", inner],
                    ["xterm", "-e", "bash", "-ilc", inner],
                ]
            )

            vuid = 0 if not prefix else verify_user_uid
            for tm in templates:
                term_bin = shutil.which(tm[0])
                if not term_bin:
                    continue
                base = os.path.basename(term_bin)
                if prefix is None and base in _TERMINAL_SKIP_WHEN_ROOT:
                    continue
                tm_use = [term_bin] + tm[1:]
                cmd = (prefix + tm_use) if prefix else tm_use
                ok, err = _spawn_gui_terminal(cmd, env)
                if not ok:
                    if err and err != "not installed":
                        last_err = err
                    continue
                if vuid is not None:
                    if not _verify_graphical_terminal(vuid, tm_use):
                        last_err = (
                            f"{tm_use[0]} exited without leaving a desktop terminal process "
                            f"(session/D-Bus?). Try **Copy shell** or install gnome-terminal/konsole."
                        )
                        continue
                opened_hint = None
                if prefix:
                    if not login_inner_nopass:
                        opened_hint = (
                            "If sudo asks for a password in this window, enter it once — "
                            "then you're in the netns."
                        )
                else:
                    bn = os.path.basename(tm_use[0])
                    if bn == "xterm":
                        opened_hint = (
                            "Opened root fallback xterm (GNOME Console cannot run as root). "
                            "Start probe via ./run-probe.sh so Terminal can use kgx first."
                        )
                return jsonify(
                    {
                        "ok": True,
                        "command": shell_cmd,
                        "hint": opened_hint,
                    }
                )

        hint = (
            "No graphical session detected for sudo (DISPLAY/WAYLAND missing). "
            "Start the probe from a desktop terminal via ./run-probe.sh, or use **Copy shell** "
            "and run the command yourself."
        )
        if not has_gui:
            last_err = f"{last_err}. {hint}" if last_err else hint

        pretty_terminal_hint = None
        if has_gui and last_err:
            pretty_terminal_hint = (
                "If kgx never appeared: check DISPLAY under sudo, or use **Copy shell**."
            )

        return jsonify(
            {
                "ok": False,
                "error": last_err or "Could not open a graphical terminal.",
                "command": shell_cmd,
                "hint": hint if not has_gui else pretty_terminal_hint,
            }
        ), 500
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


def _humanize_plan_slug(slug: str) -> str:
    """Fallback readable title when ``display_name`` is missing (underscores → spaced words)."""
    if not slug:
        return ""
    parts = [p for p in slug.replace("-", "_").split("_") if p]
    return " ".join(p.capitalize() for p in parts)


def _plan_display_title(plan: dict, slug: str) -> str:
    dn = (plan.get("display_name") or "").strip()
    if dn:
        return dn
    return _humanize_plan_slug(slug)


def _validate_run_binding(cfg: dict) -> Tuple[bool, str]:
    """Require namespace + target IP for a concrete plan run (not stored on the template)."""
    ns = (cfg.get("namespace") or "").strip()
    tip = (cfg.get("target_ip") or "").strip()
    if not ns:
        return False, "Namespace is required"
    if not tip:
        return False, "Target IP is required"
    try:
        active = list_netns()
    except Exception:
        active = []
    if ns not in active:
        return False, f'Namespace "{ns}" is not active. Isolate a USB interface first.'
    return True, ""


def _load_plan(plan_name: str) -> dict:
    plan_path = _plan_template_json(plan_name)
    with open(plan_path) as f:
        return json.load(f)


def _save_plan(plan_name: str, plan: dict) -> None:
    plan.pop("config", None)
    plan["updated_at"] = datetime.now().isoformat()
    plan_path = _plan_template_json(plan_name)
    os.makedirs(os.path.dirname(plan_path), exist_ok=True)
    with open(plan_path, "w") as f:
        json.dump(plan, f, indent=2)


def _save_run(plan_name: str, run_id: str, run_data: dict) -> None:
    """Write run.json under ``results/plan_runs/<plan>/<run_id>/``."""
    run_dir = os.path.join(PLAN_RUNS_DIR, plan_name, f"run_{run_id}")
    _makedirs_chown(run_dir)
    run_json = os.path.join(run_dir, "run.json")
    with open(run_json, "w") as f:
        json.dump(run_data, f, indent=2)
    _chown_result(run_json)


def _list_runs(plan_name: str) -> List[dict]:
    """List all runs for a plan, sorted by date descending."""
    plan_dir = os.path.join(PLAN_RUNS_DIR, plan_name)
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


def _latest_resumable_run_id(plan_name: str) -> Optional[str]:
    for r in _list_runs(plan_name):
        if r.get("status") in ("running", "partial"):
            rid = r.get("run_id")
            if rid:
                return str(rid)
    return None


def _plan_overlay_latest_run(plan_name: str) -> dict:
    """Template JSON merged with the latest run's experiment rows (for Results UI)."""
    plan_view = copy.deepcopy(_load_plan(plan_name))
    runs = _list_runs(plan_name)
    if runs:
        rid = runs[0].get("run_id")
        if rid:
            rpath = os.path.join(PLAN_RUNS_DIR, plan_name, f"run_{rid}", "run.json")
            try:
                with open(rpath) as f:
                    rd = json.load(f)
                ex = rd.get("experiments")
                if isinstance(ex, list) and ex:
                    plan_view["experiments"] = ex
            except Exception:
                pass
    plan_view.pop("config", None)
    return plan_view


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
    for plan_name in _iter_plan_template_slugs():
        plan_json = _plan_template_json(plan_name)
        if not os.path.exists(plan_json):
            continue
        try:
            with open(plan_json) as f:
                plan = json.load(f)
            exps = plan.get("experiments", [])
            plans.append({
                "name": plan_name,
                "display_name": _plan_display_title(plan, plan_name),
                "readonly": plan_name in READONLY_BUILTIN_PLANS,
                "created_at": plan.get("created_at"),
                "experiment_count": len(exps),
                "experiments_summary": _experiments_summary(exps),
                "experiments": [
                    {k: e.get(k) for k in ("id", "label", "type", "direction",
                     "protocol", "bandwidth", "bandwidth_dl", "duration", "ping_count",
                     "ping_interval", "ping_packet_size", "length_bytes", "parallel_streams",
                     "interval_s", "udp_length_mode", "udp_mtu_clamp")}
                    for e in exps
                ],
            })
        except Exception:
            pass
    return jsonify({"plans": plans})


@app.route("/api/plans", methods=["POST"])
def create_plan():
    data = request.json or {}
    display_raw = (data.get("display_name") or data.get("name") or "").strip()
    experiments_raw = data.get("experiments", [])

    if not display_raw:
        return jsonify({"ok": False, "error": "Plan title is required"}), 400

    try:
        plan_name = _slugify(display_raw)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    if plan_name in RESERVED_PLAN_SLUGS:
        return jsonify({"ok": False, "error": f"Reserved plan slug: '{plan_name}'"}), 400

    plan_json = _plan_template_json(plan_name)
    if os.path.isfile(plan_json):
        return jsonify({"ok": False, "error": f"Plan '{plan_name}' already exists"}), 409

    tmpl_parent = os.path.dirname(plan_json)
    os.makedirs(tmpl_parent, exist_ok=True)
    try:
        experiments = _expand_experiments(experiments_raw)
    except Exception as e:
        shutil.rmtree(tmpl_parent, ignore_errors=True)
        return jsonify({"ok": False, "error": f"Failed to expand experiments: {e}"}), 400

    now = datetime.now().isoformat()
    plan = {
        "version": 1,
        "name": plan_name,
        "display_name": display_raw,
        "created_at": now,
        "updated_at": now,
        "inter_exp_pause_s": max(0, int(data.get("inter_exp_pause_s") or 0)),
        "inter_repeat_pause_s": max(0, int(data.get("inter_repeat_pause_s") or 0)),
        "experiments": experiments,
    }
    with open(plan_json, "w") as f:
        json.dump(plan, f, indent=2)

    return jsonify({"ok": True, "plan_name": plan_name, "experiment_count": len(experiments)})


@app.route("/api/plans/<plan_name>", methods=["GET"])
def get_plan(plan_name):
    plan_path = _plan_template_json(plan_name)
    if not os.path.exists(plan_path):
        return jsonify({"ok": False, "error": "Plan not found"}), 404
    with open(plan_path) as f:
        data = json.load(f)
    data.pop("config", None)
    return jsonify(data)


@app.route("/api/plans/<plan_name>", methods=["PUT"])
def update_plan(plan_name):
    """Replace an existing user-saved template (built-in defaults are read-only)."""
    if plan_name in READONLY_BUILTIN_PLANS:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Built-in plans cannot be edited. Duplicate in the UI and save under a new title.",
                }
            ),
            403,
        )
    plan_path = _plan_template_json(plan_name)
    if not os.path.isfile(plan_path):
        return jsonify({"ok": False, "error": "Plan not found"}), 404
    if _plan_running.is_set():
        return jsonify({"ok": False, "error": "A plan is currently executing."}), 409
    data = request.json or {}
    try:
        experiments = _expand_experiments(data.get("experiments", []))
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to expand experiments: {exc}"}), 400
    try:
        existing = _load_plan(plan_name)
    except Exception:
        return jsonify({"ok": False, "error": "Plan not found"}), 404
    existing.pop("config", None)
    dn_in = data.get("display_name")
    if dn_in is not None:
        dn_st = str(dn_in).strip()
        if dn_st:
            existing["display_name"] = dn_st
    update = {"experiments": experiments}
    if "inter_exp_pause_s" in data:
        update["inter_exp_pause_s"] = max(0, int(data.get("inter_exp_pause_s") or 0))
    if "inter_repeat_pause_s" in data:
        update["inter_repeat_pause_s"] = max(0, int(data.get("inter_repeat_pause_s") or 0))
    existing.update(update)
    _save_plan(plan_name, existing)
    return jsonify({"ok": True, "plan_name": plan_name, "experiment_count": len(experiments)})


@app.route("/api/plans/<plan_name>", methods=["DELETE"])
def delete_plan(plan_name):
    if plan_name in READONLY_BUILTIN_PLANS:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Built-in plans cannot be deleted.",
                }
            ),
            403,
        )
    if _plan_running.is_set():
        return jsonify({"ok": False, "error": "A plan is currently executing. Abort it first."}), 409
    plan_path = _plan_template_json(plan_name)
    if not os.path.isfile(plan_path):
        return jsonify({"ok": False, "error": "Plan not found"}), 404
    shutil.rmtree(_plan_template_dir(plan_name), ignore_errors=False)
    shutil.rmtree(os.path.join(PLAN_RUNS_DIR, plan_name), ignore_errors=True)
    return jsonify({"ok": True})


@app.route("/api/plans/<plan_name>/runs", methods=["GET"])
def list_plan_runs(plan_name):
    """List all runs for a plan, sorted by date descending."""
    if not os.path.isfile(_plan_template_json(plan_name)):
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
    run_json = os.path.join(PLAN_RUNS_DIR, plan_name, f"run_{run_id}", "run.json")
    if os.path.exists(run_json):
        with open(run_json) as f:
            return jsonify(json.load(f))
    # Backward compat: try reconstructing from plan.json
    runs = _list_runs(plan_name)
    for r in runs:
        if r.get("run_id") == run_id:
            return jsonify(r)
    return jsonify({"ok": False, "error": "Run not found"}), 404


@app.route("/api/plans/<plan_name>/runs/<run_id>", methods=["DELETE"])
def delete_plan_run(plan_name, run_id):
    """Delete a plan run directory and reset its experiment states in the plan template."""
    run_dir = os.path.join(PLAN_RUNS_DIR, plan_name, f"run_{run_id}")
    if not os.path.isdir(run_dir):
        return jsonify({"ok": False, "error": "Run not found"}), 404
    shutil.rmtree(run_dir)
    # Reset plan template experiments that point to this run
    try:
        plan = _load_plan(plan_name)
        run_prefix = f"plan_runs/{plan_name}/run_{run_id}"
        changed = False
        for exp in plan.get("experiments", []):
            if (exp.get("result_path") or "").startswith(run_prefix):
                exp["state"] = "pending"
                exp["result_path"] = None
                exp["error"] = None
                exp["started_at"] = None
                exp["completed_at"] = None
                changed = True
        if changed:
            _save_plan(plan_name, plan)
    except Exception:
        pass
    return jsonify({"ok": True})


@app.route("/api/run-history", methods=["GET"])
def run_history():
    """List all runs across all plans, sorted by date descending."""
    all_runs = []
    slugs = _iter_plan_template_slugs()
    if not slugs:
        return jsonify({"runs": []})
    for plan_name in slugs:
        try:
            disp = _plan_display_title(_load_plan(plan_name), plan_name)
        except Exception:
            disp = _humanize_plan_slug(plan_name)
        for r in _list_runs(plan_name):
            exps = r.get("experiments", [])
            all_runs.append({
                "run_id": r.get("run_id"),
                "plan_name": plan_name,
                "plan_display_name": disp,
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
        pname = item.get("plan_name", "").strip()
        if not os.path.isfile(_plan_template_json(pname)):
            return jsonify({"ok": False, "error": f"Plan '{pname}' not found"}), 404
        okb, berr = _validate_run_binding({
            "namespace": item.get("namespace", ""),
            "target_ip": item.get("target_ip", ""),
        })
        if not okb:
            return jsonify({"ok": False, "error": f"Queue item '{pname}': {berr}"}), 400
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
    last_lines: List[str] = []
    seq = 0
    prev_rtt: Optional[float] = None

    for line in iter(proc.stdout.readline, ""):
        if proc.poll() is not None and not line:
            break
        line = line.strip()
        if line:
            last_lines.append(line)
            if len(last_lines) > 5:
                last_lines.pop(0)
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
                "time_s": round((seq - 1) * interval, 3),
                "rtt_ms": rtt,
                "owd_ms": round(rtt / 2, 3),
                "jitter_ms": jitter,
                "ttl": int(m_ttl.group(1)) if m_ttl else None,
                "pkt_bytes": packet_size,
            })
            socketio.emit("ping_data", {
                "seq": seq, "ms": rtt,
                "ttl": int(m_ttl.group(1)) if m_ttl else None,
            }, to=session.sid)

    proc.wait()
    with _active_lock:
        if session.sid in _active_procs and proc in _active_procs[session.sid]:
            _active_procs[session.sid].remove(proc)

    if not ping_rows and last_lines:
        # Surface ping failure (e.g. "Network is unreachable") via exception so caller marks
        # exp as failed with a meaningful error string instead of an empty completion.
        raise RuntimeError("ping failed: " + " | ".join(last_lines[-3:]))
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

def _plan_execution_thread(
    sid: str,
    plan_name: str,
    resume: bool = False,
    run_id_override: Optional[str] = None,
    run_namespace: Optional[str] = None,
    run_target_ip: Optional[str] = None,
) -> None:
    global _plan_sid
    _plan_running.set()
    _plan_sid = sid

    try:
        plan = _load_plan(plan_name)

        # Determine run_id
        if run_id_override:
            run_id = run_id_override
        elif resume:
            rid = _latest_resumable_run_id(plan_name)
            run_id = rid if rid else datetime.now().strftime("%Y%m%dT%H%M%S")
        else:
            run_id = datetime.now().strftime("%Y%m%dT%H%M%S")

        # Build run_data — a snapshot of the template experiments with runtime state
        import copy as _copy
        run_experiments = _copy.deepcopy(plan.get("experiments", []))

        existing_run: Optional[dict] = None
        run_json_path = os.path.join(PLAN_RUNS_DIR, plan_name, f"run_{run_id}", "run.json")
        started_at = datetime.now().isoformat()
        if resume and os.path.exists(run_json_path):
            with open(run_json_path) as f:
                existing_run = json.load(f)
            run_experiments = existing_run.get("experiments", run_experiments)
            if existing_run.get("started_at"):
                started_at = str(existing_run["started_at"])

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

        if not resume:
            run_binding = {
                "namespace": (run_namespace or "").strip(),
                "target_ip": (run_target_ip or "").strip(),
            }
        else:
            run_binding = {}
            if existing_run:
                run_binding = dict(existing_run.get("config") or {})
            leg = plan.get("config") or {}
            ns_guess = (run_binding.get("namespace") or leg.get("namespace") or "ue1").strip()
            tip_guess = (run_binding.get("target_ip") or leg.get("target_ip") or probe_config.DEFAULT_UPF_CLOUD_IP).strip()
            run_binding = {"namespace": ns_guess, "target_ip": tip_guess}

        ok_bind, bind_err = _validate_run_binding(run_binding)
        if not ok_bind:
            socketio.emit("plan_complete", {
                "plan_name": plan_name,
                "status": "error",
                "message": bind_err,
            }, to=sid)
            return

        ns = run_binding["namespace"]
        target_ip = run_binding["target_ip"]

        run_data = {
            "config": {"namespace": ns, "target_ip": target_ip},
            "run_id": run_id,
            "plan_name": plan_name,
            "started_at": started_at,
            "completed_at": None,
            "status": "running",
            "experiments": run_experiments,
        }
        _save_run(plan_name, run_id, run_data)

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
            bw_dl_raw = exp.get("bandwidth_dl")
            bandwidth_dl: Optional[str]
            if bw_dl_raw is None or bw_dl_raw == "":
                bandwidth_dl = None
            elif isinstance(bw_dl_raw, str):
                bandwidth_dl = bw_dl_raw
            else:
                bandwidth_dl = f"{int(bw_dl_raw)}M"
            length_bytes = int(exp.get("length_bytes") or 1200)
            parallel_streams = max(1, min(int(exp.get("parallel_streams") or 1), 64))
            interval_s = max(0.05, float(exp.get("interval_s") or 0.1))
            udp_lm = (exp.get("udp_length_mode") or "fixed").lower()
            if udp_lm not in ("omit", "auto", "fixed"):
                udp_lm = "fixed"
            udp_clamp = max(576, min(int(exp.get("udp_mtu_clamp") or 1200), 9000))
            session = BenchmarkSession(
                sid=sid, ns=ns, target_ip=target_ip, proto=proto,
                bandwidth=bandwidth, bandwidth_dl=bandwidth_dl,
                length_bytes=length_bytes, parallel_streams=parallel_streams,
                interval_s=interval_s, udp_length_mode=udp_lm,
                udp_mtu_clamp=udp_clamp,
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
                    f"plan_runs/{plan_name}/run_{run_id}/"
                    f"{exp['id']}_{exp.get('type','tp')}_"
                    f"{exp.get('direction','ul')}_{exp.get('protocol','tcp')}"
                    + (f"_{safe_bw}" if safe_bw else "")
                )
                phase_results, ping_rows = _run_single_experiment(session, exp)

                # Save CSV + metadata (no chart screenshot for plan experiments)
                result_dir = os.path.join(RESULTS_DIR, result_subpath)
                _makedirs_chown(result_dir)

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
                meta_path = os.path.join(result_dir, "metadata.json")
                with open(meta_path, "w") as f:
                    json.dump(meta, f, indent=2)
                _chown_result(meta_path)

                # Mark failed when the test produced no usable data — otherwise we'd record an
                # empty "completed" row (e.g. iperf3 exited instantly because the namespace
                # or target IP was wrong).
                produced_data = bool(phase_results) or bool(ping_rows)
                exp_dur = time.time() - exp_start
                # Compute expected duration per test type:
                #   latency: count * interval (the iperf3 "duration" field doesn't apply)
                #   throughput/bufferbloat: configured duration
                if exp.get("type") == "latency":
                    expected_dur = max(
                        float(exp.get("ping_count") or 0) * float(exp.get("ping_interval") or 0.5),
                        0.0,
                    )
                else:
                    expected_dur = float(exp.get("duration") or 0)
                too_fast = expected_dur >= 5 and exp_dur < max(2.0, expected_dur * 0.25)
                if not produced_data or too_fast:
                    exp["state"] = "failed"
                    exp["result_path"] = result_subpath if csv_written else None
                    exp["error"] = (
                        "Test produced no data (check namespace / target IP / iperf3 server)"
                        if not produced_data
                        else f"Test exited after {exp_dur:.1f}s (expected ~{expected_dur:.0f}s)"
                    )
                    exp["completed_at"] = datetime.now().isoformat()
                else:
                    exp["state"] = "completed"
                    exp["result_path"] = result_subpath
                    exp["completed_at"] = datetime.now().isoformat()

            except Exception as e:
                exp["state"] = "failed"
                exp["error"] = str(e)
                exp["completed_at"] = datetime.now().isoformat()

            completed_durations.append(time.time() - exp_start)
            _save_run(plan_name, run_id, run_data)

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

            # Pause selection: per-experiment override > plan default > legacy 2s
            exp_pause = exp.get("pause_after_s")
            if exp_pause is None:
                exp_pause = plan.get("inter_exp_pause_s", 2)
            try:
                exp_pause = max(0, int(exp_pause))
            except Exception:
                exp_pause = 2
            if exp_pause > 0:
                time.sleep(exp_pause)

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
    payload = data or {}
    plan_name = str(payload.get("plan_name", "") or "").strip()
    resume = bool(payload.get("resume", False))
    run_ns = str(payload.get("namespace", "") or "").strip()
    run_tip = str(payload.get("target_ip", "") or "").strip()

    if not plan_name:
        emit("plan_complete", {"status": "error", "message": "No plan name provided"})
        return

    if not resume:
        okb, berr = _validate_run_binding({"namespace": run_ns, "target_ip": run_tip})
        if not okb:
            emit("plan_complete", {"status": "error", "message": berr})
            return

    if not os.path.isfile(_plan_template_json(plan_name)):
        emit("plan_complete", {"status": "error", "message": f"Plan '{plan_name}' not found"})
        return

    if _plan_running.is_set():
        emit("plan_complete", {"status": "error", "message": "Another plan is already running"})
        return

    t = threading.Thread(
        target=_plan_execution_thread,
        kwargs={
            "sid": sid,
            "plan_name": plan_name,
            "resume": resume,
            "run_namespace": run_ns if not resume else None,
            "run_target_ip": run_tip if not resume else None,
        },
        daemon=True,
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
            _plan_execution_thread(
                sid,
                plan_name,
                resume=False,
                run_namespace=str(item.get("namespace", "") or "").strip() or None,
                run_target_ip=str(item.get("target_ip", "") or "").strip() or None,
            )

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

            # Delay between runs: per-plan inter_repeat_pause_s wins over global queue delay
            with _queue_lock:
                has_next = bool(_run_queue)
            plan_pause = 0
            if has_next:
                try:
                    plan_pause = int(_load_plan(plan_name).get("inter_repeat_pause_s") or 0)
                except Exception:
                    plan_pause = 0
            delay = max(plan_pause, _queue_delay_s) if has_next else 0
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
    """Reset experiments stuck in 'running' inside ``run.json`` after a crash."""
    if not os.path.isdir(PLAN_RUNS_DIR):
        return
    for plan_name in os.listdir(PLAN_RUNS_DIR):
        root = os.path.join(PLAN_RUNS_DIR, plan_name)
        if not os.path.isdir(root):
            continue
        for entry in os.listdir(root):
            if not entry.startswith("run_"):
                continue
            run_json = os.path.join(root, entry, "run.json")
            if not os.path.isfile(run_json):
                continue
            try:
                with open(run_json) as f:
                    rd = json.load(f)
                changed = False
                for exp in rd.get("experiments", []):
                    if exp.get("state") == "running":
                        exp["state"] = "failed"
                        exp["error"] = "Interrupted (server restart)"
                        changed = True
                if changed:
                    with open(run_json, "w") as f:
                        json.dump(rd, f, indent=2)
                    _chown_result(run_json)
            except Exception:
                pass


"""Live packet sniffer service.

Provides two capabilities:
1. **Live capture** -- stream tshark/tcpdump output from a specific capture
   point back to the caller line-by-line (used by the WebSocket endpoint).
2. **Path trace** -- run short simultaneous captures at every hop in the PDU
   session data path and report which segments see traffic.

Capture points:
  - br-n3 on the worker node via SSH (GTP-U from gNB)
  - n3 inside the UPF pod via kubectl exec (GTP-U arriving at UPF)
  - ogstun inside the UPF pod (decapsulated UE IP traffic)
  - n6 inside the UPF pod (traffic exiting toward Data Network)
  - br-n2 on the worker node (NGAP signaling)
"""

import asyncio
import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, AsyncIterator

from kubernetes.stream import stream as k8s_stream

from app.config import settings
from app.services.k8s_service import K8sService

log = logging.getLogger(__name__)

NS = "5g"

CAPTURE_POINTS = {
    "br-n3": {
        "label": "N3 Bridge (Worker)",
        "description": "GTP-U packets from gNB to UPF",
        "method": "ssh",
        "interface": "br-n3",
        "default_filter": "udp port 2152",
        "protocol": "GTP-U",
    },
    "upf-n3": {
        "label": "UPF N3 Interface",
        "description": "GTP-U arriving inside UPF pod",
        "method": "pod",
        "pod_app": "upf-cloud",
        "interface": "n3",
        "default_filter": "udp port 2152",
        "protocol": "GTP-U",
    },
    "upf-ogstun": {
        "label": "UPF ogstun (Tunnel)",
        "description": "Decapsulated UE IP traffic",
        "method": "pod",
        "pod_app": "upf-cloud",
        "interface": "ogstun",
        "default_filter": "",
        "protocol": "IP",
    },
    "upf-n6": {
        "label": "UPF N6 Interface",
        "description": "Traffic exiting toward Data Network",
        "method": "pod",
        "pod_app": "upf-cloud",
        "interface": "n6",
        "default_filter": "",
        "protocol": "IP",
    },
    "br-n2": {
        "label": "N2 Bridge (Worker)",
        "description": "NGAP signaling (AMF ↔ gNB)",
        "method": "ssh",
        "interface": "br-n2",
        "default_filter": "sctp",
        "protocol": "SCTP",
    },
}


def _find_upf_pod(k8s: K8sService, app: str = "upf-cloud") -> str | None:
    pods = k8s.core.list_namespaced_pod(namespace=NS, label_selector=f"app={app}")
    for p in pods.items:
        if p.metadata.deletion_timestamp:
            continue
        if p.status.phase == "Running":
            return p.metadata.name
    return None


def _ssh_capture_cmd(interface: str, bpf_filter: str, count: int, duration: int) -> list[str]:
    tcpdump_cmd = f"sudo timeout {duration} tcpdump -i {interface} -l -n"
    if count > 0:
        tcpdump_cmd += f" -c {count}"
    if bpf_filter:
        tcpdump_cmd += f" '{bpf_filter}'"
    tcpdump_cmd += " 2>&1"
    return [
        "ssh",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "LogLevel=ERROR",
        "-o", "BatchMode=yes",
        settings.worker_ssh_host,
        tcpdump_cmd,
    ]


# ---------------------------------------------------------------------------
# Live capture: SSH method (async subprocess)
# ---------------------------------------------------------------------------

async def _live_capture_ssh(
    point: dict[str, Any],
    bpf_filter: str,
    count: int,
    duration: int,
    cancel: asyncio.Event,
) -> AsyncIterator[str]:
    cmd = _ssh_capture_cmd(point["interface"], bpf_filter, count, duration)
    yield f"[sniffer] Capturing on {point['interface']} via SSH"
    yield f"[sniffer] Filter: {bpf_filter or '(none)'}"
    yield "---"

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        while not cancel.is_set():
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=2)
            except asyncio.TimeoutError:
                continue
            if not line:
                break
            yield line.decode("utf-8", errors="replace").rstrip()
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        yield "---"
        yield f"[sniffer] Capture finished (exit {proc.returncode})"


# ---------------------------------------------------------------------------
# Live capture: Pod method (k8s_stream in a thread -> asyncio.Queue)
# ---------------------------------------------------------------------------

def _pod_reader_thread(
    k8s: K8sService,
    pod_name: str,
    tcpdump_cmd: list[str],
    duration: int,
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
    stop: threading.Event,
) -> None:
    """Read from k8s_stream in a dedicated thread; push lines to the async queue."""
    resp = None
    try:
        resp = k8s_stream(
            k8s.core.connect_get_namespaced_pod_exec,
            pod_name,
            NS,
            command=tcpdump_cmd,
            stderr=True,
            stdout=True,
            stdin=False,
            tty=False,
            _preload_content=False,
            _request_timeout=duration + 10,
        )
        while resp.is_open() and not stop.is_set():
            resp.update(timeout=1)
            for channel in (1, 2, 3):
                data = resp.read_channel(channel, timeout=0)
                if data:
                    for line in data.strip().split("\n"):
                        if line.strip():
                            loop.call_soon_threadsafe(queue.put_nowait, line.strip())
    except Exception as exc:
        loop.call_soon_threadsafe(
            queue.put_nowait, f"[error] Pod capture error: {exc}"
        )
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        loop.call_soon_threadsafe(queue.put_nowait, None)


async def _live_capture_pod(
    k8s: K8sService,
    point: dict[str, Any],
    bpf_filter: str,
    count: int,
    duration: int,
    cancel: asyncio.Event,
) -> AsyncIterator[str]:
    pod_name = _find_upf_pod(k8s, point["pod_app"])
    if not pod_name:
        yield f"[error] Pod {point['pod_app']} not found or not running"
        return

    iface = point["interface"]
    tcpdump_cmd = ["timeout", str(duration), "tcpdump", "-i", iface, "-l", "-n"]
    if count > 0:
        tcpdump_cmd.extend(["-c", str(count)])
    if bpf_filter:
        tcpdump_cmd.extend(bpf_filter.split())

    yield f"[sniffer] Capturing on {iface} in pod {pod_name}"
    yield f"[sniffer] Filter: {bpf_filter or '(none)'}"
    yield "---"

    queue: asyncio.Queue[str | None] = asyncio.Queue()
    stop = threading.Event()
    loop = asyncio.get_running_loop()

    thread = threading.Thread(
        target=_pod_reader_thread,
        args=(k8s, pod_name, tcpdump_cmd, duration, queue, loop, stop),
        daemon=True,
    )
    thread.start()

    try:
        while not cancel.is_set():
            try:
                item = await asyncio.wait_for(queue.get(), timeout=2)
            except asyncio.TimeoutError:
                continue
            if item is None:
                break
            yield item
    finally:
        stop.set()
        thread.join(timeout=5)
        yield "---"
        yield "[sniffer] Capture finished"


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

async def live_capture(
    k8s: K8sService,
    point_id: str,
    bpf_filter: str | None = None,
    count: int = 0,
    duration: int = 300,
    cancel: asyncio.Event | None = None,
) -> AsyncIterator[str]:
    """Yield captured packet lines. Respects *cancel* event for clean shutdown."""
    point = CAPTURE_POINTS.get(point_id)
    if not point:
        yield f"[error] Unknown capture point: {point_id}"
        return

    if cancel is None:
        cancel = asyncio.Event()

    filt = bpf_filter if bpf_filter is not None else point["default_filter"]

    if point["method"] == "ssh":
        async for line in _live_capture_ssh(point, filt, count, duration, cancel):
            yield line
    elif point["method"] == "pod":
        async for line in _live_capture_pod(k8s, point, filt, count, duration, cancel):
            yield line


# ---------------------------------------------------------------------------
# Path trace (batch capture at all hops) -- unchanged logic, used by REST
# ---------------------------------------------------------------------------

def _run_short_capture_ssh(interface: str, bpf_filter: str, duration: int = 5) -> dict[str, Any]:
    cmd = _ssh_capture_cmd(interface, bpf_filter, count=100, duration=duration)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=duration + 8, check=False,
        )
        output = proc.stdout or ""
        lines = [l for l in output.strip().split("\n") if l.strip() and not l.startswith("tcpdump:")]

        pkt_count = 0
        capture_lines = []
        for line in lines:
            if "packets captured" in line:
                try:
                    pkt_count = int(line.split()[0])
                except (ValueError, IndexError):
                    pass
            elif "packets received" not in line and "packets dropped" not in line:
                capture_lines.append(line)
                pkt_count = max(pkt_count, len(capture_lines))

        return {
            "packets": pkt_count,
            "sample_lines": capture_lines[:5],
            "status": "active" if pkt_count > 0 else "silent",
        }
    except Exception as exc:
        return {"packets": 0, "sample_lines": [], "status": "error", "error": str(exc)}


def _run_short_capture_pod(k8s: K8sService, pod_name: str, interface: str, bpf_filter: str, duration: int = 5) -> dict[str, Any]:
    cmd = ["timeout", str(duration), "tcpdump", "-i", interface, "-l", "-n", "-c", "100"]
    if bpf_filter:
        cmd.extend(bpf_filter.split())

    try:
        output = k8s_stream(
            k8s.core.connect_get_namespaced_pod_exec,
            pod_name,
            NS,
            command=cmd,
            stderr=True,
            stdout=True,
            stdin=False,
            tty=False,
            _request_timeout=duration + 5,
        )
        lines = [l for l in output.strip().split("\n") if l.strip() and not l.startswith("tcpdump:")]

        pkt_count = 0
        capture_lines = []
        for line in lines:
            if "packets captured" in line:
                try:
                    pkt_count = int(line.split()[0])
                except (ValueError, IndexError):
                    pass
            elif "packets received" not in line and "packets dropped" not in line:
                capture_lines.append(line)
                pkt_count = max(pkt_count, len(capture_lines))

        return {
            "packets": pkt_count,
            "sample_lines": capture_lines[:5],
            "status": "active" if pkt_count > 0 else "silent",
        }
    except Exception as exc:
        return {"packets": 0, "sample_lines": [], "status": "error", "error": str(exc)}


def run_path_trace(k8s: K8sService, duration: int = 5) -> list[dict[str, Any]]:
    """Run simultaneous short captures at all data-path hops and report results."""
    upf_pod = _find_upf_pod(k8s)

    trace_points = [
        ("br-n3", CAPTURE_POINTS["br-n3"]),
        ("upf-n3", CAPTURE_POINTS["upf-n3"]),
        ("upf-ogstun", CAPTURE_POINTS["upf-ogstun"]),
        ("upf-n6", CAPTURE_POINTS["upf-n6"]),
    ]

    results = []

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {}
        for point_id, point in trace_points:
            if point["method"] == "ssh":
                fut = pool.submit(
                    _run_short_capture_ssh,
                    point["interface"],
                    point["default_filter"],
                    duration,
                )
            elif point["method"] == "pod" and upf_pod:
                fut = pool.submit(
                    _run_short_capture_pod,
                    k8s,
                    upf_pod,
                    point["interface"],
                    point["default_filter"],
                    duration,
                )
            else:
                results.append({
                    "point_id": point_id,
                    "label": point["label"],
                    "description": point["description"],
                    "protocol": point["protocol"],
                    "status": "error",
                    "error": f"Pod {point.get('pod_app', '?')} not found",
                    "packets": 0,
                    "sample_lines": [],
                })
                continue
            futures[fut] = (point_id, point)

        for fut in futures:
            point_id, point = futures[fut]
            try:
                capture = fut.result(timeout=duration + 10)
            except Exception as exc:
                capture = {"packets": 0, "sample_lines": [], "status": "error", "error": str(exc)}

            results.append({
                "point_id": point_id,
                "label": point["label"],
                "description": point["description"],
                "protocol": point["protocol"],
                **capture,
            })

    order = ["br-n3", "upf-n3", "upf-ogstun", "upf-n6"]
    results.sort(key=lambda r: order.index(r["point_id"]) if r["point_id"] in order else 99)
    return results

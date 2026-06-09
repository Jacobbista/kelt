import asyncio
import re
import logging
import threading

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.services.k8s_service import K8sService, get_k8s_service

router = APIRouter(prefix="/api/v1/ws", tags=["logs"])
log = logging.getLogger(__name__)

K8S_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})\.\d+Z\s+")
APP_TS_RE = re.compile(r"^(?:\x1b\[\d+(?:;\d+)*m)*\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}")
# Valid ANSI SGR: ESC [ <params> m — keep these to preserve colors
ANSI_CSI_RE = re.compile(r"\x1b\[[\x20-\x3f]*[\x40-\x7e]")


def _sanitize_for_terminal(raw: str) -> str:
    """Remove control chars that corrupt xterm; preserve valid ANSI sequences."""
    # Replace null and bell (0x00, 0x07)
    raw = raw.replace("\x00", "").replace("\x07", "")
    # Replace stray ESC not part of CSI; preserve valid ANSI
    result = []
    i = 0
    while i < len(raw):
        if raw[i] == "\x1b" and i + 1 < len(raw) and raw[i + 1] == "[":
            m = ANSI_CSI_RE.match(raw[i:])
            if m:
                result.append(m.group(0))
                i += m.end()
                continue
        if raw[i] in "\t\n\r":
            result.append(raw[i])
            i += 1
            continue
        c = ord(raw[i])
        if c < 0x20 or c == 0x7f:
            result.append(".")  # placeholder for other control chars
        else:
            result.append(raw[i])
        i += 1
    return "".join(result)


def _format_log_line(raw: str) -> str:
    """Strip k8s ISO timestamp. Keep Open5GS app timestamp if present. Sanitize for xterm."""
    m = K8S_TS_RE.match(raw)
    if not m:
        return _sanitize_for_terminal(raw)
    content = raw[m.end():]
    content = _sanitize_for_terminal(content)
    if APP_TS_RE.match(content):
        return content
    return f"\x1b[90m{m.group(1)}\x1b[0m {content}"


INITIAL_TAIL = 200
MAX_TAIL = 3000
# Hard cap for from_start. The Kubernetes API read_namespaced_pod_log
# call returns the WHOLE log in one response when tail_lines is unset;
# a debug-level NF can produce hundreds of MB, which blocks the call
# past LOG_CALL_TIMEOUT_S, blows the WebSocket frame limit on the
# upstream tunnel, and pins memory on the backend. Cap at FROM_START_TAIL
# and emit a banner so the operator knows older entries were dropped.
FROM_START_TAIL = 10000
POLL_INTERVAL_S = 2
LOG_CALL_TIMEOUT_S = 8
STREAM_PROGRESS_EVERY_N = 500


def _is_nf_line(raw: str) -> bool:
    m = K8S_TS_RE.match(raw)
    if not m:
        return False
    return bool(APP_TS_RE.match(raw[m.end() :]))


async def _stream_from_start(
    websocket: WebSocket,
    k8s: K8sService,
    namespace: str,
    pod: str,
    container: str | None,
) -> str | None:
    """Stream the historical tail of a pod log line by line.

    Uses the K8s API in raw streaming mode (`_preload_content=False`),
    decodes lines as they arrive, and forwards each line to the
    WebSocket. Lets the operator see progress immediately on debug-mode
    pods instead of blocking on a single huge bulk read. Returns the
    last K8s timestamp seen so the poll loop can resume from there.
    """
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    cancel = threading.Event()

    def _producer() -> None:
        kwargs: dict = {
            "name": pod,
            "namespace": namespace,
            "timestamps": True,
            "tail_lines": FROM_START_TAIL,
            "_preload_content": False,
            "_request_timeout": (5, 600),
        }
        if container:
            kwargs["container"] = container
        resp = None
        try:
            resp = k8s.core.read_namespaced_pod_log(**kwargs)
            buf = b""
            for chunk in resp.stream(amt=8192):
                if cancel.is_set():
                    break
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    try:
                        asyncio.run_coroutine_threadsafe(
                            q.put(raw.decode("utf-8", errors="replace")), loop
                        ).result(timeout=5)
                    except Exception:
                        cancel.set()
                        return
            if buf and not cancel.is_set():
                try:
                    asyncio.run_coroutine_threadsafe(
                        q.put(buf.decode("utf-8", errors="replace")), loop
                    ).result(timeout=5)
                except Exception:
                    pass
        except Exception as exc:
            log.debug("from_start stream error for %s/%s: %s", namespace, pod, exc)
        finally:
            try:
                if resp is not None:
                    resp.release_conn()
            except Exception:
                pass
            try:
                asyncio.run_coroutine_threadsafe(q.put(None), loop)
            except Exception:
                pass

    threading.Thread(target=_producer, daemon=True).start()

    last_ts_local: str | None = None
    sent = 0
    batch: list[str] = []

    async def _flush() -> None:
        nonlocal sent
        if batch:
            await websocket.send_text("\n".join(batch))
            sent += len(batch)
            batch.clear()

    try:
        while True:
            line = await q.get()
            if line is None:
                break
            ts = line.split(" ", 1)[0] if " " in line else ""
            formatted = _format_log_line(line)
            if _is_nf_line(line):
                await _flush()
                await websocket.send_text(formatted)
                sent += 1
            else:
                batch.append(formatted)
            if ts:
                last_ts_local = ts
            if sent and sent % STREAM_PROGRESS_EVERY_N == 0:
                await _flush()
                await websocket.send_text(
                    f"\x1b[90m[dashboard] streamed {sent} lines...\x1b[0m"
                )
        await _flush()
    finally:
        cancel.set()

    return last_ts_local


@router.websocket("/logs/{namespace}/{pod}")
async def logs_stream(
    websocket: WebSocket,
    namespace: str,
    pod: str,
    k8s: K8sService = Depends(get_k8s_service),
) -> None:
    await websocket.accept()
    container = websocket.query_params.get("container")
    tail_param = websocket.query_params.get("tail")
    from_start = websocket.query_params.get("from_start", "").lower() in ("1", "true", "yes")

    tail_lines = INITIAL_TAIL
    if tail_param is not None:
        try:
            n = int(tail_param)
            tail_lines = min(max(n, 1), MAX_TAIL)
        except ValueError:
            pass

    last_ts: str | None = None
    sent_count = 0

    try:
        if from_start:
            await websocket.send_text(
                f"\x1b[90m[dashboard] streaming last {FROM_START_TAIL} lines from pod start "
                f"(older entries truncated)...\x1b[0m"
            )
            last_ts = await _stream_from_start(websocket, k8s, namespace, pod, container)
            await websocket.send_text(
                "\x1b[90m[dashboard] backlog done, following live updates...\x1b[0m"
            )
            if last_ts is None:
                # Stream returned nothing (fresh container, no history yet).
                # Mark initial fetch as completed so the poll loop switches
                # to since_seconds instead of re-running the bulk read.
                last_ts = ""
                sent_count = 1

        while True:
            kwargs: dict = {
                "name": pod,
                "namespace": namespace,
                "timestamps": True,
            }
            if container:
                kwargs["container"] = container

            if last_ts is None:
                kwargs["tail_lines"] = tail_lines
            elif last_ts == "":
                # First poll after an empty from_start stream: no since
                # filter yet so the first new line is captured.
                pass
            else:
                kwargs["since_seconds"] = POLL_INTERVAL_S + 2

            try:
                log_text: str = await asyncio.wait_for(
                    asyncio.to_thread(k8s.core.read_namespaced_pod_log, **kwargs),
                    timeout=LOG_CALL_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                log.debug("log poll timeout for %s/%s", namespace, pod)
                log_text = ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.debug("log poll error for %s/%s: %s", namespace, pod, exc)
                log_text = ""

            if log_text and log_text.strip():
                batch: list[str] = []  # consecutive non-NF lines sent together

                async def _flush_batch() -> None:
                    nonlocal sent_count
                    if batch:
                        await websocket.send_text("\n".join(batch))
                        sent_count += 1
                        batch.clear()

                effective_last = last_ts if last_ts else None
                for line in log_text.strip().split("\n"):
                    ts = line.split(" ", 1)[0] if " " in line else ""
                    if effective_last is None or ts > effective_last:
                        formatted = _format_log_line(line)
                        if _is_nf_line(line):
                            await _flush_batch()
                            await websocket.send_text(formatted)
                            sent_count += 1
                        else:
                            batch.append(formatted)
                        if ts:
                            last_ts = ts
                            effective_last = ts

                await _flush_batch()

            if sent_count == 0 and last_ts is None:
                await websocket.send_text("[dashboard] waiting for log output...")
                last_ts = ""

            await asyncio.sleep(POLL_INTERVAL_S)
    except (WebSocketDisconnect, asyncio.CancelledError):
        return
    except Exception as exc:
        log.warning("log stream error for %s/%s: %s", namespace, pod, exc)
        try:
            await websocket.send_text(f"[dashboard-error] {exc}")
            await websocket.close(code=1011)
        except Exception:
            pass

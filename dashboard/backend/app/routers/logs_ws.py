import asyncio
import re
import logging

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
POLL_INTERVAL_S = 2
LOG_CALL_TIMEOUT_S = 8


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
        while True:
            kwargs: dict = {
                "name": pod,
                "namespace": namespace,
                "timestamps": True,
            }
            if container:
                kwargs["container"] = container

            if last_ts is None:
                # from_start: fetch full log from pod start (no tail_lines)
                if not from_start:
                    kwargs["tail_lines"] = tail_lines
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

                def _is_nf_line(raw: str) -> bool:
                    m = K8S_TS_RE.match(raw)
                    if not m:
                        return False
                    return bool(APP_TS_RE.match(raw[m.end() :]))

                async def _flush_batch() -> None:
                    nonlocal sent_count
                    if batch:
                        await websocket.send_text("\n".join(batch))
                        sent_count += 1
                        batch.clear()

                for line in log_text.strip().split("\n"):
                    ts = line.split(" ", 1)[0] if " " in line else ""
                    if last_ts is None or ts > last_ts:
                        formatted = _format_log_line(line)
                        if _is_nf_line(line):
                            await _flush_batch()
                            await websocket.send_text(formatted)
                            sent_count += 1
                        else:
                            batch.append(formatted)
                        if ts:
                            last_ts = ts

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

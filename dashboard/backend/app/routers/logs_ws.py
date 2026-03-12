import asyncio
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.services.k8s_service import K8sService, get_k8s_service

router = APIRouter(prefix="/api/v1/ws", tags=["logs"])
log = logging.getLogger(__name__)

INITIAL_TAIL = 200
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
                kwargs["tail_lines"] = INITIAL_TAIL
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
                for line in log_text.strip().split("\n"):
                    ts = line.split(" ", 1)[0] if " " in line else ""
                    if last_ts is None or ts > last_ts:
                        await websocket.send_text(line)
                        sent_count += 1
                        if ts:
                            last_ts = ts

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

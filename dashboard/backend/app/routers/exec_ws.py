"""WebSocket endpoint for interactive pod exec (shell access)."""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from kubernetes.stream import stream as k8s_stream

from app.services.k8s_service import K8sService, get_k8s_service

router = APIRouter(prefix="/api/v1/ws", tags=["exec"])
log = logging.getLogger(__name__)

READ_INTERVAL_S = 0.05  # 50ms polling for stdout/stderr


@router.websocket("/exec/{namespace}/{pod}")
async def exec_stream(
    websocket: WebSocket,
    namespace: str,
    pod: str,
    k8s: K8sService = Depends(get_k8s_service),
) -> None:
    await websocket.accept()

    container = websocket.query_params.get("container")
    command = websocket.query_params.get("command", "/bin/sh")
    cmd = command.split() if " " in command else [command]

    exec_kwargs = {
        "name": pod,
        "namespace": namespace,
        "command": cmd,
        "stderr": True,
        "stdin": True,
        "stdout": True,
        "tty": True,
        "_preload_content": False,
    }
    if container:
        exec_kwargs["container"] = container

    try:
        resp = await asyncio.to_thread(
            k8s_stream,
            k8s.core.connect_get_namespaced_pod_exec,
            **exec_kwargs,
        )
    except Exception as exc:
        log.warning("exec connect failed for %s/%s: %s", namespace, pod, exc)
        await websocket.send_text(json.dumps({"type": "stdout", "data": f"[error] Failed to exec: {exc}\r\n"}))
        await websocket.close(code=1011)
        return

    async def read_output():
        """Read stdout/stderr from the k8s stream and send to client."""
        try:
            while resp.is_open():
                resp.update(timeout=READ_INTERVAL_S)
                out = ""
                if resp.peek_stdout():
                    out += resp.read_stdout()
                if resp.peek_stderr():
                    out += resp.read_stderr()
                if out:
                    await websocket.send_text(json.dumps({"type": "stdout", "data": out}))
                else:
                    await asyncio.sleep(READ_INTERVAL_S)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            log.debug("exec read error: %s", exc)
        finally:
            try:
                await websocket.send_text(json.dumps({"type": "stdout", "data": "\r\n[session ended]\r\n"}))
                await websocket.close()
            except Exception:
                pass

    reader_task = asyncio.create_task(read_output())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "stdin" and resp.is_open():
                resp.write_stdin(msg.get("data", ""))
            elif msg.get("type") == "resize" and resp.is_open():
                # xterm.js resize — k8s stream supports resize via control channel
                cols = msg.get("cols", 80)
                rows = msg.get("rows", 24)
                try:
                    resp.write_channel(4, json.dumps({"Width": cols, "Height": rows}))
                except Exception:
                    pass
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as exc:
        log.warning("exec ws error for %s/%s: %s", namespace, pod, exc)
    finally:
        reader_task.cancel()
        try:
            resp.close()
        except Exception:
            pass

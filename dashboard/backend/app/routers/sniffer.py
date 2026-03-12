"""Sniffer endpoints: live capture WebSocket + path trace REST."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from app.services.k8s_service import K8sService, get_k8s_service
from app.services.sniffer_service import (
    CAPTURE_POINTS,
    live_capture,
    run_path_trace,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["sniffer"])


@router.get("/sniffer/points")
def list_capture_points() -> list[dict[str, Any]]:
    """Return available capture points with metadata."""
    return [
        {
            "id": pid,
            "label": p["label"],
            "description": p["description"],
            "protocol": p["protocol"],
            "default_filter": p["default_filter"],
            "interface": p["interface"],
        }
        for pid, p in CAPTURE_POINTS.items()
    ]


@router.post("/sniffer/trace")
def path_trace(
    duration: int = 5,
    k8s: K8sService = Depends(get_k8s_service),
) -> list[dict[str, Any]]:
    """Run simultaneous captures at all data-path hops."""
    duration = min(max(duration, 3), 15)
    try:
        return run_path_trace(k8s, duration=duration)
    except Exception as exc:
        log.exception("Path trace failed")
        raise HTTPException(500, detail=str(exc)) from exc


async def _ws_receive_loop(websocket: WebSocket, cancel: asyncio.Event) -> None:
    """Monitor the WebSocket for client disconnect / stop commands."""
    try:
        while True:
            msg = await websocket.receive_text()
            if msg.strip().lower() == "stop":
                cancel.set()
                return
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
        cancel.set()


@router.websocket("/ws/sniffer/{point_id}")
async def sniffer_stream(
    websocket: WebSocket,
    point_id: str,
    k8s: K8sService = Depends(get_k8s_service),
) -> None:
    """Stream live packet capture from a specific capture point."""
    await websocket.accept()

    bpf_filter = websocket.query_params.get("filter")
    try:
        count = int(websocket.query_params.get("count", "0"))
    except ValueError:
        count = 0
    try:
        duration = int(websocket.query_params.get("duration", "300"))
    except ValueError:
        duration = 300

    duration = min(max(duration, 5), 600)
    count = max(count, 0)

    cancel = asyncio.Event()

    recv_task = asyncio.create_task(_ws_receive_loop(websocket, cancel))

    try:
        async for line in live_capture(k8s, point_id, bpf_filter, count, duration, cancel):
            if cancel.is_set():
                break
            try:
                await websocket.send_text(line)
            except (WebSocketDisconnect, RuntimeError):
                cancel.set()
                break
    except (WebSocketDisconnect, asyncio.CancelledError):
        cancel.set()
    except Exception as exc:
        log.warning("Sniffer WS error for %s: %s", point_id, exc)
        try:
            await websocket.send_text(f"[error] {exc}")
        except Exception:
            pass
    finally:
        cancel.set()
        recv_task.cancel()
        try:
            await recv_task
        except (asyncio.CancelledError, Exception):
            pass

    try:
        await websocket.close()
    except Exception:
        pass

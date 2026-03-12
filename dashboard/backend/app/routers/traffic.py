"""WebSocket endpoint streaming real-time OVS bridge traffic deltas."""

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.traffic_service import get_traffic_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["traffic"])

POLL_INTERVAL_S = 2


@router.websocket("/ws/traffic/intensity")
async def traffic_intensity_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    svc = get_traffic_service()
    try:
        while True:
            try:
                deltas = await asyncio.get_event_loop().run_in_executor(
                    None, svc.get_counter_deltas,
                )
            except Exception as exc:
                log.debug("Counter fetch error: %s", exc)
                deltas = {}

            await websocket.send_json({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "links": deltas,
            })
            await asyncio.sleep(POLL_INTERVAL_S)
    except (WebSocketDisconnect, asyncio.CancelledError):
        return
    except Exception:
        log.exception("Traffic WS error")

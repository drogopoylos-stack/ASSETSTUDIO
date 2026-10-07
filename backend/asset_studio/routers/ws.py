"""WebSocket live feed: job progress, logs, asset-created, agent + system stats.

Clients connect to ``/ws``; on connect they receive a ``hello`` plus recent
history, then a continuous stream of :class:`ProgressEvent` JSON payloads.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..events import bus
from ..models import ProgressEvent, WSEventType

router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    q = bus.subscribe()
    try:
        await websocket.send_text(ProgressEvent(type=WSEventType.hello).model_dump_json())
        for ev in bus.recent():
            await websocket.send_text(ev.model_dump_json())
        while True:
            ev = await q.get()
            await websocket.send_text(ev.model_dump_json())
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        bus.unsubscribe(q)

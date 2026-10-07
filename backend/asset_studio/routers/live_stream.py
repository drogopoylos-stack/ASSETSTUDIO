"""The agent's live tab, as a stream a person can watch.

Its own router beside routers/live.py, so the stream and the agent's calls share a prefix and
nothing else: a person watching goes through here, and nothing here can open, navigate or change
the tab. The Engine window's Game tab plays `/api/live/stream/ws` and polls
`/api/live/stream/status` every three seconds; `/api/live/stream` is the same picture as MJPEG,
for a script or a plain `<img>`.

The HTTP routes are plain `def`. FastAPI runs them in its threadpool, and the stream's body is a
sync generator stepped there too, so a viewer waiting for a frame never waits on the event loop.
The WebSocket steps the same generator through `run_in_threadpool`, for the same reason.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse

from .. import live_stream

router = APIRouter(prefix="/api/live", tags=["live"])


@router.get("/stream")
def stream(project: str, fps: int = 0, quality: int = 0, max_w: int = 0):
    """MJPEG of the project's existing live tab. 0 means "the setting"; out of range is clamped.

    404 when no agent has the game open — the stream never opens a tab to have something to show.
    """
    v, err, code = live_stream.open_viewer(project, fps, quality, max_w)
    if v is None:
        return JSONResponse(err, status_code=code)
    return StreamingResponse(live_stream.frames(v), media_type=live_stream.MEDIA_TYPE,
                             headers=dict(live_stream.HEADERS))


async def _until_closed(sock: WebSocket) -> None:
    while True:
        msg = await sock.receive()
        if msg.get("type") == "websocket.disconnect":
            return


@router.websocket("/stream/ws")
async def stream_ws(sock: WebSocket, project: str = "", fps: int = 0, quality: int = 0, max_w: int = 0):
    """The same frames for the Game tab: one binary message per frame, the JPEG itself.

    WHY NOT THE MJPEG ROUTE. Its response never ends, so in the Studio's own page it held one of the
    browser's six HTTP/1.1 connections to this host for as long as the view was open, and every
    poll and call in the app queued on the five left. A WebSocket is not in that pool. (Another
    host name for the same backend was the other way out, and it is a trap here: `localhost` tries
    ::1 first, and Windows takes 2 s to refuse it — measured.)

    Refused (no tab, the link off, too many viewers): one text message with the same JSON the HTTP
    route answers, then a close with 4000 + the HTTP status (4404, 4429)."""
    await sock.accept()
    v, err, code = await run_in_threadpool(live_stream.open_viewer, project, fps, quality, max_w)
    if v is None:
        try:
            await sock.send_json(err)
            await sock.close(code=4000 + int(code))
        except Exception:                                           # noqa: BLE001
            pass
        return
    gen = live_stream.jpeg_frames(v)
    closed = asyncio.ensure_future(_until_closed(sock))
    try:
        while not closed.done():
            jpeg = await run_in_threadpool(next, gen, None)
            if jpeg is None:
                break                          # the screencast stopped: the tab closed, idle, or off
            if jpeg and not closed.done():
                await sock.send_bytes(jpeg)
    except Exception:                                               # noqa: BLE001
        pass                                   # the person closed the tab mid-send
    finally:
        closed.cancel()
        await run_in_threadpool(gen.close)     # its `finally` lets the viewer go…
        live_stream.leave(v)                   # …which a generator closed before its first step never ran
        try:
            await sock.close()
        except Exception:                                           # noqa: BLE001
            pass


@router.get("/stream/status")
def stream_status(project: str):
    """{live, url, engine?, streaming, viewers, fps_measured, last_frame_ms_ago, agent_active, …}"""
    return live_stream.status(project)

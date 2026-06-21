"""
WebSocket endpoint — the live state connection.

Two distinct event streams over one socket (PRD 1.5):
  - discrete state-change events ("queue_changed", "now_playing"), fired on
    mutation commit and fanned out via the events bus;
  - a periodic "position" tick during playback.

Auth is by ``?token=`` query param (browsers can't set headers on a WebSocket);
the token is the same opaque session token issued by ``/auth/pin``.
"""

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services import auth_service, events, playback

logger = logging.getLogger(__name__)

router = APIRouter()

TICK_INTERVAL = 3.0  # seconds between position ticks


def _now_playing_event() -> dict:
    controller = playback.get_controller()
    entry = controller.now_playing() if controller else None
    if entry is None:
        return {"type": "now_playing", "track": None}
    return {
        "type": "now_playing",
        "track": {
            "title": entry.title,
            "artist": entry.uploader,
            "duration": entry.duration,
            "thumbnail": entry.thumbnail,
            "webpage_url": entry.webpage_url or None,
            "requested_by": entry.requested_by_name,
            "track_id": entry.track_id,
        },
    }


@router.websocket("/ws")
async def ws(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if not token or auth_service.resolve_session(token) is None:
        await websocket.close(code=1008)  # policy violation
        return

    await websocket.accept()
    queue = events.subscribe()

    async def forward_events():
        while True:
            event = await queue.get()
            await websocket.send_json(event)

    async def position_ticks():
        while True:
            await asyncio.sleep(TICK_INTERVAL)
            controller = playback.get_controller()
            pos = controller.position_seconds() if controller else None
            if pos is not None:
                await websocket.send_json({"type": "position", "seconds": pos})

    async def receiver():
        # We don't expect inbound messages; this exists to detect disconnect.
        while True:
            await websocket.receive_text()

    # Send the current state immediately so a fresh client isn't blank.
    try:
        await websocket.send_json(_now_playing_event())
    except Exception:
        events.unsubscribe(queue)
        return

    tasks = [
        asyncio.create_task(forward_events()),
        asyncio.create_task(position_ticks()),
        asyncio.create_task(receiver()),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket loop error")
    finally:
        for t in tasks:
            t.cancel()
        events.unsubscribe(queue)

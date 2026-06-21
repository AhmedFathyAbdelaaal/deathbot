"""
Shared queue orchestration — the single write path for queue mutations.

Both the Discord cog and the web API call into here. Each mutation: writes
through ``queue_store`` (Postgres, one transaction), fires a WebSocket-bound
event *after* the commit, and — for adds — asks the playback controller to
start if it was idle. "Skip", "add", "remove", etc. are therefore one code path
regardless of whether Discord or the web triggered them (PRD 1.5).

The upcoming queue lives in Postgres; the now-playing track and voice state
live in the controller's in-memory playback engine.
"""

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import QueueItem
from services import events, playback, queue_store

logger = logging.getLogger(__name__)


async def add(
    session: AsyncSession,
    *,
    added_by: Optional[int] = None,
    track_id: Optional[int] = None,
    source_url: Optional[str] = None,
    title: Optional[str] = None,
    artist: Optional[str] = None,
) -> "tuple[QueueItem, bool]":
    """Append a track and start playback if the engine was idle. Returns the
    persisted item and whether playback started immediately."""
    item = await queue_store.add_item(
        session,
        added_by=added_by,
        track_id=track_id,
        source_url=source_url,
        title=title,
        artist=artist,
    )
    events.broadcast({"type": "queue_changed"})

    started = False
    controller = playback.get_controller()
    if controller is not None:
        try:
            started = await controller.ensure_playing()
        except Exception:
            logger.exception("ensure_playing failed after enqueue")
    return item, started


async def remove(session: AsyncSession, item_id: int) -> bool:
    ok = await queue_store.remove_item(session, item_id)
    if ok:
        events.broadcast({"type": "queue_changed"})
    return ok


async def reorder(session: AsyncSession, item_id: int, new_index: int) -> bool:
    ok = await queue_store.reorder(session, item_id, new_index)
    if ok:
        events.broadcast({"type": "queue_changed"})
    return ok


async def shuffle(session: AsyncSession) -> int:
    n = await queue_store.shuffle(session)
    events.broadcast({"type": "queue_changed"})
    return n


async def skip() -> bool:
    """Skip the now-playing track. The engine's after-callback advances to the
    next Postgres item and broadcasts on its own."""
    controller = playback.get_controller()
    if controller is None:
        return False
    return await controller.skip_current()


async def stop(session: AsyncSession) -> bool:
    """Clear the queue and stop playback. Returns True if anything was stopped
    or cleared."""
    cleared = await queue_store.clear(session)
    stopped = False
    controller = playback.get_controller()
    if controller is not None:
        stopped = await controller.stop_all()
    events.broadcast({"type": "queue_changed"})
    events.broadcast({"type": "now_playing", "track": None})
    return stopped or cleared > 0

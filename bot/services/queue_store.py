"""
Postgres-backed queue store — the source of truth for the upcoming queue.

Pure data access over the ``queue_items`` table: ordered list, append, pop the
next item, remove, reorder, shuffle, clear. The now-playing track is NOT stored
here — it lives in the playback engine's in-memory state (PRD 1.4: Postgres is
the queue's source of truth; in-memory is live playback mechanics only).

Positions are kept as a contiguous ``0..n-1`` sequence; every mutation
renumbers so the order is always unambiguous. Mutators commit their own
transaction (PRD 1.5: one Postgres transaction per mutation). Callers fire the
WebSocket broadcast *after* the commit returns.
"""

import logging
import random
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import QueueItem

logger = logging.getLogger(__name__)


async def list_queue(session: AsyncSession) -> list[QueueItem]:
    """Return the upcoming queue, ordered by position."""
    return list(
        (await session.execute(select(QueueItem).order_by(QueueItem.position))).scalars()
    )


async def count(session: AsyncSession) -> int:
    return int((await session.execute(select(func.count(QueueItem.id)))).scalar() or 0)


async def _renumber(session: AsyncSession) -> None:
    """Rewrite positions to a contiguous 0..n-1 sequence in current order."""
    items = list(
        (await session.execute(select(QueueItem).order_by(QueueItem.position))).scalars()
    )
    for idx, item in enumerate(items):
        if item.position != idx:
            item.position = idx


async def add_item(
    session: AsyncSession,
    *,
    added_by: Optional[int] = None,
    track_id: Optional[int] = None,
    source_url: Optional[str] = None,
    title: Optional[str] = None,
    artist: Optional[str] = None,
) -> QueueItem:
    """Append a track to the tail and commit. Returns the persisted item."""
    max_pos = (await session.execute(select(func.max(QueueItem.position)))).scalar()
    position = 0 if max_pos is None else max_pos + 1
    item = QueueItem(
        added_by=added_by,
        position=position,
        track_id=track_id,
        source_url=source_url,
        title=title,
        artist=artist,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def pop_next(session: AsyncSession) -> Optional[QueueItem]:
    """Remove and return the lowest-position item (the next to play), or None if
    the queue is empty. The returned object is detached but its attributes
    remain readable. Commits."""
    item = (
        await session.execute(select(QueueItem).order_by(QueueItem.position).limit(1))
    ).scalar_one_or_none()
    if item is None:
        return None
    # Snapshot the object so its attributes survive the delete + commit.
    session.expunge(item)
    await session.execute(delete(QueueItem).where(QueueItem.id == item.id))
    await _renumber(session)
    await session.commit()
    return item


async def remove_item(session: AsyncSession, item_id: int) -> bool:
    """Remove a specific queued item by id. Returns False if not found. Commits."""
    item = (
        await session.execute(select(QueueItem).where(QueueItem.id == item_id))
    ).scalar_one_or_none()
    if item is None:
        return False
    await session.delete(item)
    await _renumber(session)
    await session.commit()
    return True


async def reorder(session: AsyncSession, item_id: int, new_index: int) -> bool:
    """Move an item to ``new_index`` (clamped) within the queue. Returns False if
    the item isn't found. Commits."""
    items = list(
        (await session.execute(select(QueueItem).order_by(QueueItem.position))).scalars()
    )
    moving = next((it for it in items if it.id == item_id), None)
    if moving is None:
        return False
    items.remove(moving)
    new_index = max(0, min(new_index, len(items)))
    items.insert(new_index, moving)
    for idx, item in enumerate(items):
        item.position = idx
    await session.commit()
    return True


async def shuffle(session: AsyncSession) -> int:
    """Randomize the order of the upcoming queue. Returns the item count. Commits."""
    items = list((await session.execute(select(QueueItem))).scalars())
    if not items:
        return 0
    order = list(range(len(items)))
    random.shuffle(order)
    for item, pos in zip(items, order):
        item.position = pos
    await session.commit()
    return len(items)


async def clear(session: AsyncSession) -> int:
    """Empty the queue. Returns the number removed. Commits."""
    n = await count(session)
    await session.execute(delete(QueueItem))
    await session.commit()
    return n

"""
Playlist service — named, shared playlists built from Library tracks.

Playlists are shared (not per-user) and reference ``tracks`` rows. "Queue all"
appends every playlist track onto the live queue through the same store the
rest of the app uses, then nudges playback if idle. Positions within a playlist
are kept contiguous, like the queue.
"""

import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Playlist, PlaylistTrack, Track
from services import events, playback, queue_store

logger = logging.getLogger(__name__)


async def create(session: AsyncSession, name: str, created_by: Optional[int]) -> Playlist:
    pl = Playlist(name=name, created_by=created_by)
    session.add(pl)
    await session.commit()
    await session.refresh(pl)
    return pl


async def list_with_counts(session: AsyncSession) -> "list[tuple[Playlist, int]]":
    rows = await session.execute(
        select(Playlist, func.count(PlaylistTrack.id))
        .outerjoin(PlaylistTrack, PlaylistTrack.playlist_id == Playlist.id)
        .group_by(Playlist.id)
        .order_by(Playlist.created_at)
    )
    return [(pl, count) for pl, count in rows.all()]


async def get(session: AsyncSession, playlist_id: int) -> Optional[Playlist]:
    return await session.get(Playlist, playlist_id)


async def get_tracks(session: AsyncSession, playlist_id: int) -> "list[tuple[PlaylistTrack, Track]]":
    rows = await session.execute(
        select(PlaylistTrack, Track)
        .join(Track, Track.id == PlaylistTrack.track_id)
        .where(PlaylistTrack.playlist_id == playlist_id)
        .order_by(PlaylistTrack.position)
    )
    return list(rows.all())


async def rename(session: AsyncSession, playlist_id: int, name: str) -> Optional[Playlist]:
    pl = await session.get(Playlist, playlist_id)
    if pl is None:
        return None
    pl.name = name
    await session.commit()
    await session.refresh(pl)
    return pl


async def delete(session: AsyncSession, playlist_id: int) -> bool:
    pl = await session.get(Playlist, playlist_id)
    if pl is None:
        return False
    # playlist_tracks cascade on the FK (ondelete="CASCADE").
    await session.delete(pl)
    await session.commit()
    return True


async def add_track(
    session: AsyncSession, playlist_id: int, track_id: int
) -> Optional[PlaylistTrack]:
    """Append a Library track to the playlist tail. Returns None if the
    playlist or track does not exist."""
    if await session.get(Playlist, playlist_id) is None:
        return None
    if await session.get(Track, track_id) is None:
        return None

    max_pos = (
        await session.execute(
            select(func.max(PlaylistTrack.position)).where(
                PlaylistTrack.playlist_id == playlist_id
            )
        )
    ).scalar()
    position = 0 if max_pos is None else max_pos + 1

    pt = PlaylistTrack(playlist_id=playlist_id, track_id=track_id, position=position)
    session.add(pt)
    await session.commit()
    await session.refresh(pt)
    return pt


async def _renumber(session: AsyncSession, playlist_id: int) -> None:
    rows = list(
        (
            await session.execute(
                select(PlaylistTrack)
                .where(PlaylistTrack.playlist_id == playlist_id)
                .order_by(PlaylistTrack.position)
            )
        ).scalars()
    )
    for idx, pt in enumerate(rows):
        if pt.position != idx:
            pt.position = idx


async def remove_track(session: AsyncSession, playlist_track_id: int) -> bool:
    pt = await session.get(PlaylistTrack, playlist_track_id)
    if pt is None:
        return False
    playlist_id = pt.playlist_id
    await session.delete(pt)
    await _renumber(session, playlist_id)
    await session.commit()
    return True


async def reorder(session: AsyncSession, playlist_track_id: int, new_index: int) -> bool:
    pt = await session.get(PlaylistTrack, playlist_track_id)
    if pt is None:
        return False
    rows = list(
        (
            await session.execute(
                select(PlaylistTrack)
                .where(PlaylistTrack.playlist_id == pt.playlist_id)
                .order_by(PlaylistTrack.position)
            )
        ).scalars()
    )
    rows.remove(next(r for r in rows if r.id == pt.id))
    new_index = max(0, min(new_index, len(rows)))
    rows.insert(new_index, pt)
    for idx, row in enumerate(rows):
        row.position = idx
    await session.commit()
    return True


async def queue_all(
    session: AsyncSession, playlist_id: int, added_by: Optional[int]
) -> Optional[int]:
    """Append every track in the playlist onto the live queue, then start
    playback if idle. Returns the number queued, or None if no such playlist."""
    if await session.get(Playlist, playlist_id) is None:
        return None

    pairs = await get_tracks(session, playlist_id)
    for pt, track in pairs:
        await queue_store.add_item(
            session,
            added_by=added_by,
            track_id=track.id,
            title=track.title,
            artist=track.artist,
        )
    events.broadcast({"type": "queue_changed"})

    controller = playback.get_controller()
    if controller is not None:
        try:
            await controller.ensure_playing()
        except Exception:
            logger.exception("ensure_playing failed after queue_all")
    return len(pairs)

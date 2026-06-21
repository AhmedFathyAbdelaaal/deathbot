"""
Queue routes — the web app's view of and control over the live queue.

All mutations go through the shared ``queue_service`` (same path Discord uses),
so the now-playing-skip and every add/remove/reorder is one code path
regardless of origin (PRD 1.5). Now-playing comes from the in-memory playback
controller; the upcoming list comes from Postgres.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from db.models import Track, User
from services import playback, queue_service, queue_store

router = APIRouter(prefix="/queue", tags=["queue"])


class AddRequest(BaseModel):
    track_id: Optional[int] = None
    source_url: Optional[str] = None
    title: Optional[str] = None
    artist: Optional[str] = None


class MoveRequest(BaseModel):
    position: int


class QueueItemOut(BaseModel):
    id: int
    position: int
    title: Optional[str] = None
    artist: Optional[str] = None
    source_url: Optional[str] = None
    track_id: Optional[int] = None
    added_by: Optional[int] = None


class NowPlayingOut(BaseModel):
    title: str
    artist: Optional[str] = None
    duration: Optional[int] = None
    thumbnail: Optional[str] = None
    webpage_url: Optional[str] = None
    requested_by: Optional[str] = None
    track_id: Optional[int] = None


class QueueOut(BaseModel):
    now_playing: Optional[NowPlayingOut] = None
    queue: list[QueueItemOut]


def _now_playing_out() -> Optional[NowPlayingOut]:
    controller = playback.get_controller()
    entry = controller.now_playing() if controller else None
    if entry is None:
        return None
    return NowPlayingOut(
        title=entry.title,
        artist=entry.uploader,
        duration=int(entry.duration) if entry.duration is not None else None,
        thumbnail=entry.thumbnail,
        webpage_url=entry.webpage_url or None,
        requested_by=entry.requested_by_name,
        track_id=entry.track_id,
    )


@router.get("", response_model=QueueOut)
async def get_queue(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    items = await queue_store.list_queue(db)
    return QueueOut(
        now_playing=_now_playing_out(),
        queue=[
            QueueItemOut(
                id=it.id,
                position=it.position,
                title=it.title,
                artist=it.artist,
                source_url=it.source_url,
                track_id=it.track_id,
                added_by=it.added_by,
            )
            for it in items
        ],
    )


@router.post("")
async def add_to_queue(
    body: AddRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not body.track_id and not body.source_url:
        raise HTTPException(status_code=400, detail="track_id or source_url is required")

    title, artist = body.title, body.artist
    if body.track_id:
        track = await db.get(Track, body.track_id)
        if track is None:
            raise HTTPException(status_code=404, detail="Track not found")
        title = title or track.title
        artist = artist or track.artist

    item, started = await queue_service.add(
        db,
        added_by=user.id,
        track_id=body.track_id,
        source_url=body.source_url,
        title=title,
        artist=artist,
    )
    return {"id": item.id, "position": item.position, "started_now": started}


@router.delete("/{item_id}")
async def remove_from_queue(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    if not await queue_service.remove(db, item_id):
        raise HTTPException(status_code=404, detail="Queue item not found")
    return {"ok": True}


@router.post("/{item_id}/move")
async def move_in_queue(
    item_id: int,
    body: MoveRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    if not await queue_service.reorder(db, item_id, body.position):
        raise HTTPException(status_code=404, detail="Queue item not found")
    return {"ok": True}


@router.post("/skip")
async def skip(_: User = Depends(get_current_user)):
    return {"skipped": await queue_service.skip()}


@router.post("/shuffle")
async def shuffle(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    return {"count": await queue_service.shuffle(db)}


@router.post("/stop")
async def stop(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    return {"ok": await queue_service.stop(db)}

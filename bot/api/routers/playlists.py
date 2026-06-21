"""
Playlist routes — CRUD, track membership, and queue-all.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from db.models import User
from services import playlist_service

router = APIRouter(prefix="/playlists", tags=["playlists"])


class CreateRequest(BaseModel):
    name: str


class RenameRequest(BaseModel):
    name: str


class AddTrackRequest(BaseModel):
    track_id: int


class MoveRequest(BaseModel):
    position: int


class PlaylistOut(BaseModel):
    id: int
    name: str
    created_by: Optional[int] = None
    track_count: int = 0


class PlaylistTrackOut(BaseModel):
    playlist_track_id: int
    track_id: int
    position: int
    title: str
    artist: Optional[str] = None
    duration_seconds: Optional[int] = None


class PlaylistDetailOut(BaseModel):
    id: int
    name: str
    created_by: Optional[int] = None
    tracks: list[PlaylistTrackOut]


@router.get("", response_model=list[PlaylistOut])
async def list_playlists(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    return [
        PlaylistOut(id=pl.id, name=pl.name, created_by=pl.created_by, track_count=count)
        for pl, count in await playlist_service.list_with_counts(db)
    ]


@router.post("", response_model=PlaylistOut)
async def create_playlist(
    body: CreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    pl = await playlist_service.create(db, body.name, user.id)
    return PlaylistOut(id=pl.id, name=pl.name, created_by=pl.created_by, track_count=0)


@router.get("/{playlist_id}", response_model=PlaylistDetailOut)
async def get_playlist(
    playlist_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    pl = await playlist_service.get(db, playlist_id)
    if pl is None:
        raise HTTPException(status_code=404, detail="Playlist not found")
    pairs = await playlist_service.get_tracks(db, playlist_id)
    return PlaylistDetailOut(
        id=pl.id,
        name=pl.name,
        created_by=pl.created_by,
        tracks=[
            PlaylistTrackOut(
                playlist_track_id=pt.id,
                track_id=track.id,
                position=pt.position,
                title=track.title,
                artist=track.artist,
                duration_seconds=track.duration_seconds,
            )
            for pt, track in pairs
        ],
    )


@router.patch("/{playlist_id}", response_model=PlaylistOut)
async def rename_playlist(
    playlist_id: int,
    body: RenameRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    pl = await playlist_service.rename(db, playlist_id, body.name)
    if pl is None:
        raise HTTPException(status_code=404, detail="Playlist not found")
    return PlaylistOut(id=pl.id, name=pl.name, created_by=pl.created_by)


@router.delete("/{playlist_id}")
async def delete_playlist(
    playlist_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    if not await playlist_service.delete(db, playlist_id):
        raise HTTPException(status_code=404, detail="Playlist not found")
    return {"ok": True}


@router.post("/{playlist_id}/tracks")
async def add_track(
    playlist_id: int,
    body: AddTrackRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    pt = await playlist_service.add_track(db, playlist_id, body.track_id)
    if pt is None:
        raise HTTPException(status_code=404, detail="Playlist or track not found")
    return {"playlist_track_id": pt.id, "position": pt.position}


@router.delete("/{playlist_id}/tracks/{playlist_track_id}")
async def remove_track(
    playlist_id: int,
    playlist_track_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    if not await playlist_service.remove_track(db, playlist_track_id):
        raise HTTPException(status_code=404, detail="Playlist track not found")
    return {"ok": True}


@router.post("/{playlist_id}/tracks/{playlist_track_id}/move")
async def move_track(
    playlist_id: int,
    playlist_track_id: int,
    body: MoveRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    if not await playlist_service.reorder(db, playlist_track_id, body.position):
        raise HTTPException(status_code=404, detail="Playlist track not found")
    return {"ok": True}


@router.post("/{playlist_id}/queue")
async def queue_all(
    playlist_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    count = await playlist_service.queue_all(db, playlist_id, user.id)
    if count is None:
        raise HTTPException(status_code=404, detail="Playlist not found")
    return {"queued": count}

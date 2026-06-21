"""
Library routes — browse, search, upload, edit, and remove tracks.

Upload flow (PRD 1.3): the client POSTs the file; the server streams it to the
uploads volume (enforcing the size cap server-side), reads ID3 tags for
pre-fill, and creates the track. The "manual review before confirming" step is
the client showing the returned pre-filled title/artist for the user to edit
and PATCH if needed — no re-upload of the file required.
"""

import os
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

import config
from api.deps import get_current_user, get_db
from db.models import Track, User
from services import library_service

router = APIRouter(prefix="/library", tags=["library"])


class TrackOut(BaseModel):
    id: int
    title: str
    artist: Optional[str] = None
    duration_seconds: Optional[int] = None
    uploaded_by: Optional[int] = None


class TrackUpdate(BaseModel):
    title: Optional[str] = None
    artist: Optional[str] = None


def _out(t: Track) -> TrackOut:
    return TrackOut(
        id=t.id,
        title=t.title,
        artist=t.artist,
        duration_seconds=t.duration_seconds,
        uploaded_by=t.uploaded_by,
    )


@router.get("", response_model=list[TrackOut])
async def list_library(
    q: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    tracks = await library_service.search(db, q) if q else await library_service.list_tracks(db)
    return [_out(t) for t in tracks]


@router.post("", response_model=TrackOut)
async def upload(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    artist: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        ext = library_service.validate_ext(file.filename)
    except library_service.InvalidFileType as e:
        raise HTTPException(status_code=400, detail=str(e))

    dest = library_service._dest_path(ext)
    try:
        await library_service.stream_to_disk(file, dest, config.UPLOAD_MAX_BYTES)
    except library_service.FileTooLarge:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {config.UPLOAD_MAX_BYTES} byte limit",
        )

    tags = library_service.read_tags(dest)
    fallback_title = os.path.splitext(file.filename)[0]
    track = await library_service.create_track(
        db,
        uploaded_by=user.id,
        file_path=dest,
        title=(title or tags.get("title") or fallback_title),
        artist=(artist or tags.get("artist")),
        duration_seconds=tags.get("duration"),
    )
    return _out(track)


@router.patch("/{track_id}", response_model=TrackOut)
async def edit_track(
    track_id: int,
    body: TrackUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    track = await library_service.update(db, track_id, title=body.title, artist=body.artist)
    if track is None:
        raise HTTPException(status_code=404, detail="Track not found")
    return _out(track)


@router.delete("/{track_id}")
async def delete_track(
    track_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    if not await library_service.delete(db, track_id):
        raise HTTPException(status_code=404, detail="Track not found")
    return {"ok": True}

"""
Library service — the shared upload store.

Owns file validation, streaming-to-disk with a hard size cap, ID3 tag reading
for metadata pre-fill, and the ``tracks`` table CRUD. Files land in
``UPLOADS_PATH`` (a Coolify persistent volume); only uploads live in the
Library — pasted links are never saved here (PRD 1.3).
"""

import logging
import os
import uuid
from typing import Optional

from mutagen import File as MutagenFile
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from db.models import Track

logger = logging.getLogger(__name__)

ALLOWED_EXTS = {".mp3", ".wav", ".flac", ".m4a"}
_CHUNK = 1024 * 1024  # 1 MiB streaming chunks


class InvalidFileType(Exception):
    pass


class FileTooLarge(Exception):
    pass


def validate_ext(filename: str) -> str:
    """Return the lowercased extension if allowed, else raise InvalidFileType."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_EXTS:
        raise InvalidFileType(
            f"Unsupported file type '{ext or '?'}'. Allowed: {', '.join(sorted(ALLOWED_EXTS))}"
        )
    return ext


def _dest_path(ext: str) -> str:
    """A unique absolute path under UPLOADS_PATH, preserving the extension."""
    os.makedirs(config.UPLOADS_PATH, exist_ok=True)
    return os.path.join(config.UPLOADS_PATH, f"{uuid.uuid4().hex}{ext}")


async def stream_to_disk(upload_file, dest_path: str, max_bytes: int) -> int:
    """Stream an UploadFile to disk in chunks, enforcing the byte cap as we go
    (never trusting any client-declared size). Removes the partial file and
    raises FileTooLarge if the cap is exceeded. Returns bytes written."""
    written = 0
    try:
        with open(dest_path, "wb") as f:
            while True:
                chunk = await upload_file.read(_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise FileTooLarge()
                f.write(chunk)
    except FileTooLarge:
        _safe_remove(dest_path)
        raise
    except Exception:
        _safe_remove(dest_path)
        raise
    return written


def _safe_remove(path: str) -> None:
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        logger.warning(f"Could not remove file: {path}")


def read_tags(path: str) -> dict:
    """Best-effort ID3/metadata read for pre-fill. Returns {title, artist,
    duration} with missing values omitted; never raises."""
    try:
        m = MutagenFile(path, easy=True)
        if m is None:
            return {}
        out: dict = {}
        title = (m.get("title") or [None])[0]
        artist = (m.get("artist") or [None])[0]
        if title:
            out["title"] = title
        if artist:
            out["artist"] = artist
        if getattr(m, "info", None) is not None and getattr(m.info, "length", None):
            out["duration"] = int(m.info.length)
        return out
    except Exception as e:
        logger.warning(f"Tag read failed for {path}: {e}")
        return {}


async def create_track(
    session: AsyncSession,
    *,
    uploaded_by: Optional[int],
    file_path: str,
    title: str,
    artist: Optional[str],
    duration_seconds: Optional[int],
) -> Track:
    track = Track(
        title=title,
        artist=artist,
        file_path=file_path,
        uploaded_by=uploaded_by,
        duration_seconds=duration_seconds,
    )
    session.add(track)
    await session.commit()
    await session.refresh(track)
    return track


async def list_tracks(session: AsyncSession) -> list[Track]:
    return list(
        (await session.execute(select(Track).order_by(Track.title))).scalars()
    )


async def search(session: AsyncSession, query: str) -> list[Track]:
    like = f"%{query}%"
    return list(
        (
            await session.execute(
                select(Track)
                .where(or_(Track.title.ilike(like), Track.artist.ilike(like)))
                .order_by(Track.title)
            )
        ).scalars()
    )


async def get(session: AsyncSession, track_id: int) -> Optional[Track]:
    return await session.get(Track, track_id)


async def update(
    session: AsyncSession,
    track_id: int,
    *,
    title: Optional[str] = None,
    artist: Optional[str] = None,
) -> Optional[Track]:
    track = await session.get(Track, track_id)
    if track is None:
        return None
    if title is not None:
        track.title = title
    if artist is not None:
        track.artist = artist
    await session.commit()
    await session.refresh(track)
    return track


async def delete(session: AsyncSession, track_id: int) -> bool:
    track = await session.get(Track, track_id)
    if track is None:
        return False
    path = track.file_path
    await session.delete(track)
    await session.commit()
    _safe_remove(path)
    return True

"""
Shared queue service.

Single write path for every queue mutation. Discord cogs call into this
module today; the web API (Phase 2) will call into the *same* functions, so
that "skip the now-playing track" — or any other mutation — is one code path
regardless of whether it originated in Discord or the web app.

These functions operate on a ``GuildMusicState`` (defined in ``cogs.music``)
via duck typing. ``GuildMusicState`` owns the in-memory *playback* mechanics
(voice client, FFmpeg source, inactivity timer); this module owns the *queue*
operations on top of it. No Postgres yet — that arrives in Phase 2, behind this
same interface, so cogs won't need to change again.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # avoid a runtime import cycle (cogs.music imports this module)
    from cogs.music import GuildMusicState, QueueEntry

logger = logging.getLogger(__name__)


@dataclass
class EnqueueResult:
    """Outcome of an enqueue: whether playback started now, and the resulting
    1-based queue position (0 when playback started immediately)."""

    started_now: bool
    position: int


def is_active(state: "GuildMusicState") -> bool:
    """True if something is currently playing or paused."""
    vc = state.voice_client
    return bool(vc and (vc.is_playing() or vc.is_paused()))


async def enqueue(state: "GuildMusicState", entry: "QueueEntry") -> EnqueueResult:
    """Add one track. Starts playback immediately if idle, otherwise appends
    to the tail. This is the common single-track add path."""
    if not is_active(state):
        await state._play_entry(entry)
        return EnqueueResult(started_now=True, position=0)
    state.queue.append(entry)
    return EnqueueResult(started_now=False, position=len(state.queue))


def enqueue_tail(state: "GuildMusicState", entry: "QueueEntry") -> int:
    """Append to the back of the queue without touching playback. Returns the
    new 1-based position."""
    state.queue.append(entry)
    return len(state.queue)


def enqueue_front(state: "GuildMusicState", entry: "QueueEntry") -> None:
    """Insert at the front of the queue so it plays soonest."""
    state.queue.insert(0, entry)


def skip(state: "GuildMusicState") -> bool:
    """Stop the current track; the playback after-callback advances to the
    next. Returns False if nothing is playing."""
    vc = state.voice_client
    if not vc or not vc.is_playing():
        return False
    vc.stop()
    return True


def stop(state: "GuildMusicState") -> bool:
    """Stop playback and clear the queue. Returns False if not connected."""
    if not state.voice_client:
        return False
    state.cleanup()
    return True


def shuffle(state: "GuildMusicState") -> int:
    """Shuffle the pending queue (the current track is untouched). Returns the
    number of tracks shuffled, or -1 if the queue is empty."""
    if not state.queue:
        return -1
    random.shuffle(state.queue)
    return len(state.queue)


def cycle_loop(state: "GuildMusicState") -> int:
    """Advance loop mode Off → Track → Queue → Off. Returns the new mode."""
    state.loop_mode = state.loop_mode + 1
    return state.loop_mode


def snapshot(state: "GuildMusicState") -> tuple[Optional["QueueEntry"], list["QueueEntry"]]:
    """Return ``(current, queued_entries)`` — a copy of the queue for display
    or serialization, plus the now-playing entry."""
    return state.current, list(state.queue)

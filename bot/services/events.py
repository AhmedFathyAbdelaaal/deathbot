"""
In-process event bus for live state broadcasts.

Queue mutations publish discrete state-change events here after their DB commit;
the WebSocket endpoint (Phase 2 step 15) subscribes and forwards them to web
clients. Kept separate from the periodic position tick — different frequency,
different consumers (PRD 1.5).

Single-process, single event loop, so a plain set of asyncio.Queues is enough.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

_subscribers: "set[asyncio.Queue]" = set()


def subscribe() -> "asyncio.Queue":
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.add(q)
    return q


def unsubscribe(q: "asyncio.Queue") -> None:
    _subscribers.discard(q)


def broadcast(event: dict) -> None:
    """Fan an event out to all subscribers. Never blocks or raises — a slow or
    full subscriber simply drops this event rather than stalling a mutation."""
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Dropping event for a full subscriber queue")

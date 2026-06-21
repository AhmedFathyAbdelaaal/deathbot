"""
Pins and sessions — the web platform's access model.

Flow: ``/website`` in Discord issues a per-user pin (stored on ``users``); the
web app POSTs that pin to ``/auth/pin`` to mint an opaque session token. The
token store is in-memory and deliberately not persisted — it matches the
project's low-friction, single-process trust model (no pin TTL, no role
system). A bot restart simply means re-entering the pin.
"""

import logging
import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from db.models import User

logger = logging.getLogger(__name__)

_DIGITS = "0123456789"

# token -> user_id
_sessions: dict[str, int] = {}


def _generate_pin() -> str:
    return "".join(secrets.choice(_DIGITS) for _ in range(config.PIN_LENGTH))


async def issue_pin(session: AsyncSession, discord_id: int, display_name: str) -> str:
    """Create or update the user row and assign a fresh pin that is unique
    among all currently-active pins. Commits and returns the new pin."""
    user = (
        await session.execute(select(User).where(User.discord_id == discord_id))
    ).scalar_one_or_none()

    active_pins = set(
        (
            await session.execute(
                select(User.current_pin).where(User.current_pin.is_not(None))
            )
        ).scalars()
    )

    pin = _generate_pin()
    attempts = 0
    while pin in active_pins and attempts < 50:
        pin = _generate_pin()
        attempts += 1

    now = datetime.now(timezone.utc)
    if user is None:
        user = User(
            discord_id=discord_id,
            display_name=display_name,
            current_pin=pin,
            pin_issued_at=now,
        )
        session.add(user)
    else:
        user.display_name = display_name
        user.current_pin = pin
        user.pin_issued_at = now

    await session.commit()
    logger.info(f"Issued pin for discord_id={discord_id}")
    return pin


async def validate_pin(session: AsyncSession, pin: str) -> Optional[User]:
    """Return the user holding this pin, or None."""
    return (
        await session.execute(select(User).where(User.current_pin == pin))
    ).scalar_one_or_none()


def create_session(user_id: int) -> str:
    """Mint and store an opaque session token for a validated user."""
    token = secrets.token_urlsafe(32)
    _sessions[token] = user_id
    return token


def resolve_session(token: str) -> Optional[int]:
    """Return the user_id behind a session token, or None if unknown."""
    return _sessions.get(token)

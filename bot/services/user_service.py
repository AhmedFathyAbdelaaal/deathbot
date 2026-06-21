"""
User upsert — maps a Discord identity to a ``users`` row.

Both ``/website`` (pin issuance) and ``/play`` (queue attribution) need the
Discord user to exist in Postgres. This is the one shared place that happens.
"""

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import User

logger = logging.getLogger(__name__)


async def upsert(session: AsyncSession, discord_id: int, display_name: str) -> User:
    """Return the user for this Discord id, creating it (or refreshing the
    display name) as needed. Commits."""
    user: Optional[User] = (
        await session.execute(select(User).where(User.discord_id == discord_id))
    ).scalar_one_or_none()

    if user is None:
        user = User(discord_id=discord_id, display_name=display_name)
        session.add(user)
        await session.commit()
        await session.refresh(user)
    elif user.display_name != display_name:
        user.display_name = display_name
        await session.commit()

    return user

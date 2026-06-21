"""
Shared FastAPI dependencies: DB sessions and the current authenticated user.
"""

from typing import AsyncGenerator, Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.base import get_sessionmaker
from db.models import User
from services import auth_service


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a DB session, or 503 if the database never initialized."""
    sm = get_sessionmaker()
    if sm is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    async with sm() as session:
        yield session


async def get_current_user(
    authorization: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the ``Authorization: Bearer <token>`` session header to a user.
    Raises 401 on any failure."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing session token")
    token = authorization.split(" ", 1)[1].strip()
    user_id = auth_service.resolve_session(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User no longer exists")
    return user

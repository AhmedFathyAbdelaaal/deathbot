"""
Auth routes — pin check (login) and current-user lookup.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from db.models import User
from services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


class PinRequest(BaseModel):
    pin: str


class UserOut(BaseModel):
    id: int
    display_name: str


class SessionOut(BaseModel):
    token: str
    user: UserOut


@router.post("/pin", response_model=SessionOut)
async def check_pin(body: PinRequest, db: AsyncSession = Depends(get_db)):
    """Exchange a valid pin for a session token."""
    user = await auth_service.validate_pin(db, body.pin.strip())
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid pin")
    token = auth_service.create_session(user.id)
    return SessionOut(token=token, user=UserOut(id=user.id, display_name=user.display_name))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    """Return the session's user — used by the web app to validate a stored token."""
    return UserOut(id=user.id, display_name=user.display_name)

import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import (
    MOBILE_ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
    create_mobile_access_token,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.routers.auth import me
from app.schemas.auth import UserResponse

# Mobile app auth: Bearer access token + rotating refresh token, no cookies.
# The website keeps using /auth/login and its httponly cookie.
router = APIRouter()


class TokenRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    # Access token lifetime in seconds
    expires_in: int


def _invalid_refresh() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token.")


async def _issue_pair(db: AsyncSession, user_id: int, family_id: Optional[str] = None) -> TokenPairResponse:
    refresh = new_refresh_token()
    db.add(RefreshToken(
        user_id=user_id,
        family_id=family_id or str(uuid.uuid4()),
        token_hash=hash_refresh_token(refresh),
        expires_at=datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    ))
    await db.flush()
    return TokenPairResponse(
        access_token=create_mobile_access_token(user_id),
        refresh_token=refresh,
        expires_in=MOBILE_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


async def _revoke_family(db: AsyncSession, family_id: str) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.utcnow())
    )


# ── POST /v1/auth/token ───────────────────────────────────────────────────────
@router.post("/token", response_model=TokenPairResponse)
async def issue_token(body: TokenRequest, db: AsyncSession = Depends(get_db)):
    # Same checks and messages as the website's POST /auth/login
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is inactive.")

    return await _issue_pair(db, user.id)


# ── POST /v1/auth/refresh ─────────────────────────────────────────────────────
@router.post("/refresh", response_model=TokenPairResponse)
async def refresh_token(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    # Lock the row so two concurrent refreshes with the same token serialize:
    # the second sees used_at set and is treated as reuse
    row = await db.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_refresh_token(body.refresh_token))
        .with_for_update()
    )
    if not row or row.revoked_at is not None:
        raise _invalid_refresh()

    now = datetime.utcnow()
    if row.used_at is not None:
        # A rotated-out token came back: assume it leaked and end the session.
        # Commit before raising, since get_db rolls back on exceptions.
        await _revoke_family(db, row.family_id)
        await db.commit()
        raise _invalid_refresh()

    if row.expires_at <= now:
        raise _invalid_refresh()

    user = await db.get(User, row.user_id)
    if not user or not user.is_active:
        await _revoke_family(db, row.family_id)
        await db.commit()
        raise _invalid_refresh()

    row.used_at = now
    return await _issue_pair(db, user.id, row.family_id)


# ── POST /v1/auth/logout ──────────────────────────────────────────────────────
@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    # Holding the refresh token is the proof; an unknown token is not an error
    # so a logout retried after a lost response still succeeds
    row = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(body.refresh_token))
    )
    if row:
        await _revoke_family(db, row.family_id)


# ── GET /v1/auth/me ───────────────────────────────────────────────────────────
# The website's handler, exposed here so the app reaches it under /v1
router.add_api_route("/me", me, methods=["GET"], response_model=UserResponse)

from typing import Optional

from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.database import get_db
from app.core.security import decode_access_token, decode_bearer_token
from app.models.user import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# auto_error=False: a missing header falls through to the website's cookie
bearer_scheme = HTTPBearer(auto_error=False, description="Mobile app access token from POST /v1/auth/token")


# ── Dependency: optional user (no error if unauthenticated) ──────────────────
async def get_optional_user(
    access_token: Optional[str] = Cookie(default=None),
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    try:
        return await get_current_user(access_token, bearer, db)
    except HTTPException:
        return None


# ── Dependency: get current user from Bearer header (app) or cookie (website) ─
async def get_current_user(
    access_token: Optional[str] = Cookie(default=None),
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    # A Bearer header wins and never falls back to the cookie, so the app
    # always gets a 401 it can act on (refresh) when its token has expired
    if bearer is not None:
        user_id = decode_bearer_token(bearer.credentials)
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    else:
        if not access_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
        user_id = decode_access_token(access_token)
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token.")

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")
    return user


# ── Require authenticated user ────────────────────────────────────────────────
async def require_user(current_user: User = Depends(get_current_user)) -> User:
    return current_user


# ── Require admin ─────────────────────────────────────────────────────────────
async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required."
        )
    return current_user


# ── Require trainer ───────────────────────────────────────────────────────────
async def require_trainer(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_trainer and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Trainer access required."
        )
    return current_user

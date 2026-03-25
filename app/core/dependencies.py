from typing import Optional

from fastapi import Cookie, Depends, HTTPException, status

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.user import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession



# ── Dependency: get current user from cookie ──────────────────────────────────
async def get_current_user(
    access_token: Optional[str] = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
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

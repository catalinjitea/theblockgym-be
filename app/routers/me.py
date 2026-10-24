from typing import Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_user
from app.models.user import User
from app.models.user_preferences import UserPreferences

# Member settings used by the mobile app. Mounted at /v1/me only.
router = APIRouter()

Language = Literal["ro", "en"]


class PreferencesResponse(BaseModel):
    # None until the member saves a preference; the app then keeps its own default
    language: Optional[Language] = None


class PreferencesUpdate(BaseModel):
    # Omitted fields stay as they are
    language: Optional[Language] = None


# ── GET /v1/me/preferences ────────────────────────────────────────────────────
@router.get("/preferences", response_model=PreferencesResponse)
async def get_preferences(
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    prefs = await db.get(UserPreferences, current_user.id)
    return PreferencesResponse(language=prefs.language if prefs else None)


# ── PATCH /v1/me/preferences ──────────────────────────────────────────────────
@router.patch("/preferences", response_model=PreferencesResponse)
async def update_preferences(
    body: PreferencesUpdate,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    prefs = await db.get(UserPreferences, current_user.id, with_for_update=True)
    if prefs is None:
        prefs = UserPreferences(user_id=current_user.id)
        db.add(prefs)
    if body.language is not None:
        prefs.language = body.language
    await db.flush()
    return PreferencesResponse(language=prefs.language)

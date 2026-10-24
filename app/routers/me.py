from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_user
from app.models.push_device import PushDevice
from app.models.user import User
from app.models.user_preferences import UserPreferences

# Member settings and devices used by the mobile app. Mounted at /v1/me only.
router = APIRouter()

Language = Literal["ro", "en"]


class PreferencesResponse(BaseModel):
    # None until the member saves a preference; the app then keeps its own default
    language: Optional[Language] = None
    # Push notifications, on unless turned off
    class_reminders: bool = True
    expiry_alerts: bool = True


class PreferencesUpdate(BaseModel):
    # Omitted fields stay as they are
    language: Optional[Language] = None
    class_reminders: Optional[bool] = None
    expiry_alerts: Optional[bool] = None


def _to_response(prefs: Optional[UserPreferences]) -> PreferencesResponse:
    if prefs is None:
        return PreferencesResponse()
    return PreferencesResponse(
        language=prefs.language,
        class_reminders=prefs.class_reminders,
        expiry_alerts=prefs.expiry_alerts,
    )


# ── GET /v1/me/preferences ────────────────────────────────────────────────────
@router.get("/preferences", response_model=PreferencesResponse)
async def get_preferences(
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    return _to_response(await db.get(UserPreferences, current_user.id))


# ── PATCH /v1/me/preferences ──────────────────────────────────────────────────
@router.patch("/preferences", response_model=PreferencesResponse)
async def update_preferences(
    body: PreferencesUpdate,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    prefs = await db.get(UserPreferences, current_user.id, with_for_update=True)
    if prefs is None:
        prefs = UserPreferences(user_id=current_user.id, class_reminders=True, expiry_alerts=True)
        db.add(prefs)
    if body.language is not None:
        prefs.language = body.language
    if body.class_reminders is not None:
        prefs.class_reminders = body.class_reminders
    if body.expiry_alerts is not None:
        prefs.expiry_alerts = body.expiry_alerts
    await db.flush()
    return _to_response(prefs)


# ── POST /v1/me/devices ───────────────────────────────────────────────────────
class DeviceRequest(BaseModel):
    expo_push_token: str = Field(min_length=1, max_length=255)
    platform: Literal["ios", "android"]
    locale: Optional[str] = Field(default=None, max_length=20)


@router.post("/devices", status_code=status.HTTP_204_NO_CONTENT)
async def register_device(
    body: DeviceRequest,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    # Idempotent: the app registers on every launch. A token already linked to
    # another member moves to this one (someone else logged in on that phone).
    device = await db.scalar(
        select(PushDevice).where(PushDevice.expo_push_token == body.expo_push_token).with_for_update()
    )
    now = datetime.utcnow()
    if device is None:
        db.add(PushDevice(
            user_id=current_user.id,
            expo_push_token=body.expo_push_token,
            platform=body.platform,
            locale=body.locale,
            created_at=now,
            last_seen_at=now,
        ))
    else:
        device.user_id = current_user.id
        device.platform = body.platform
        device.locale = body.locale
        device.last_seen_at = now
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── DELETE /v1/me/devices/{token} ─────────────────────────────────────────────
@router.delete("/devices/{expo_push_token}", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_device(
    expo_push_token: str,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    # Only the member's own device; an unknown token is not an error
    await db.execute(
        delete(PushDevice).where(
            PushDevice.expo_push_token == expo_push_token,
            PushDevice.user_id == current_user.id,
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

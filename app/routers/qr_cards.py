import io
import uuid
import zipfile
from datetime import datetime

import qrcode
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.dependencies import require_admin
from app.core.websocket import manager
from app.models.membership import Membership
from app.models.qr_card import QRCard
from app.models.user import User
from app.schemas.qr_card import ActivateQRCardRequest, GenerateQRCardsRequest, QRCardResponse

router = APIRouter()


def generate_qr_image(code: str) -> bytes:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(code)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── WS /admin/qrcards/ws ─────────────────────────────────────────────────────
@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep connection alive
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ── POST /admin/qrcards/verify/{code} ────────────────────────────────────────
@router.post("/verify/{code}")
async def verify_qr_card(
    code: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(QRCard)
        .where(QRCard.code == code)
        .options(
            selectinload(QRCard.membership).selectinload(Membership.user)
        )
    )
    card = result.scalar_one_or_none()

    # Card not found
    if not card:
        payload = {
            "status": "invalid",
            "code": code,
            "message": "Card necunoscut.",
        }
        await manager.broadcast(payload)
        return payload

    # Card inactive (not yet assigned)
    if not card.is_active or not card.membership:
        payload = {
            "status": "inactive",
            "code": code,
            "message": "Card inactiv — nu este asociat unui abonament.",
        }
        await manager.broadcast(payload)
        return payload

    membership = card.membership
    user = membership.user

    # Membership expired
    if membership.end_date < datetime.utcnow():
        payload = {
            "status": "expired",
            "code": code,
            "message": "Abonament expirat.",
            "member_name": user.name,
            "plan": membership.plan,
            "expiry_date": membership.end_date.isoformat(),
        }
        await manager.broadcast(payload)
        return payload

    # All good
    payload = {
        "status": "valid",
        "code": code,
        "message": "Acces permis.",
        "member_name": user.name,
        "plan": membership.plan,
        "expiry_date": membership.end_date.isoformat(),
    }
    await manager.broadcast(payload)
    return payload


# ── POST /admin/qrcards/generate ─────────────────────────────────────────────
@router.post("/generate")
async def generate_qr_cards(
    body: GenerateQRCardsRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if body.count < 1 or body.count > 200:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Count must be between 1 and 200.")

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for _ in range(body.count):
            code = f"CARD_{uuid.uuid4().hex[:12].upper()}"
            card = QRCard(code=code, is_active=False)
            db.add(card)
            png_bytes = generate_qr_image(code)
            zf.writestr(f"{code}.png", png_bytes)

    await db.flush()
    zip_buffer.seek(0)

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=qr_cards_{body.count}.zip"},
    )


# ── GET /admin/qrcards/ ───────────────────────────────────────────────────────
@router.get("/", response_model=list[QRCardResponse])
async def list_qr_cards(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(QRCard).order_by(QRCard.created_at.desc()))
    return result.scalars().all()


# ── PATCH /admin/qrcards/{code}/activate ─────────────────────────────────────
@router.patch("/{code}/activate", response_model=QRCardResponse)
async def activate_qr_card(
    code: str,
    body: ActivateQRCardRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(QRCard).where(QRCard.code == code))
    card = result.scalar_one_or_none()
    if not card:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="QR card not found.")
    if card.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Card is already active.")

    result = await db.execute(select(Membership).where(Membership.id == body.membership_id))
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found.")

    result = await db.execute(select(QRCard).where(QRCard.membership_id == body.membership_id))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This membership already has a QR card assigned.")

    card.membership_id = body.membership_id
    card.is_active = True
    return card


# ── PATCH /admin/qrcards/{code}/deactivate ───────────────────────────────────
@router.patch("/{code}/deactivate", response_model=QRCardResponse)
async def deactivate_qr_card(
    code: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(QRCard).where(QRCard.code == code))
    card = result.scalar_one_or_none()
    if not card:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="QR card not found.")

    card.is_active = False
    card.membership_id = None
    return card

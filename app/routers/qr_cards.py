import io
import uuid
import zipfile
from datetime import datetime

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.membership import compute_end_date
from app.core.dependencies import require_admin
from app.core.websocket import manager
from app.models.membership import Membership
from app.models.membership_plan import MembershipPlan
from app.models.qr_card import QRCard
from app.models.scan_entry import ScanEntry
from app.models.user import User
from app.schemas.qr_card import ActivateQRCardRequest, GenerateQRCardsRequest, QRCardResponse, RenewQRCardRequest, ScanEntryResponse

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

    async def record_and_broadcast(status: str, qr_card_id=None) -> None:
        db.add(ScanEntry(code=code, status=status, qr_card_id=qr_card_id))

    # Card not found
    if not card:
        payload = {
            "status": "invalid",
            "code": code,
            "message": "Card necunoscut.",
        }
        await record_and_broadcast("invalid")
        await manager.broadcast(payload)
        return payload

    # Card inactive (not yet assigned)
    if not card.is_active or not card.membership:
        payload = {
            "status": "inactive",
            "code": code,
            "message": "Card inactiv — nu este asociat unui abonament.",
        }
        await record_and_broadcast("inactive", card.id)
        await manager.broadcast(payload)
        return payload

    membership = card.membership
    user = membership.user

    now = datetime.utcnow()

    # Membership not yet started
    if membership.start_date > now:
        payload = {
            "status": "inactive",
            "code": code,
            "message": "Abonament neînceput.",
        }
        await record_and_broadcast("inactive", card.id)
        await manager.broadcast(payload)
        return payload

    # Membership expired — check for an advance-purchased membership that is now active
    if membership.end_date < now:
        advance_result = await db.execute(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.start_date <= now,
                Membership.end_date >= now,
                Membership.id != membership.id,
            ).order_by(Membership.start_date.asc()).limit(1)
        )
        advance = advance_result.scalar_one_or_none()
        if advance:
            card.membership_id = advance.id
            membership = advance
        else:
            payload = {
                "status": "expired",
                "code": code,
                "message": "Abonament expirat.",
                "member_name": f"{user.first_name} {user.last_name}",
                "plan": membership.plan,
                "start_date": membership.start_date.isoformat(),
                "expiry_date": membership.end_date.isoformat(),
            }
            await record_and_broadcast("expired", card.id)
            await manager.broadcast(payload)
            return payload

    # All good
    payload = {
        "status": "valid",
        "code": code,
        "message": "Acces permis.",
        "member_name": f"{user.first_name} {user.last_name}",
        "plan": membership.plan,
        "start_date": membership.start_date.isoformat(),
        "expiry_date": membership.end_date.isoformat(),
    }
    await record_and_broadcast("valid", card.id)
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
            code = f"QRCARD_{uuid.uuid4().hex[:12].upper()}"
            card = QRCard(code=code, type="physical", is_active=False)
            db.add(card)
            png_bytes = generate_qr_image(code[2:])
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


# ── GET /admin/qrcards/entries ───────────────────────────────────────────────
@router.get("/entries", response_model=list[ScanEntryResponse])
async def list_scan_entries(
    response: Response,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    total = (await db.execute(select(func.count()).select_from(ScanEntry))).scalar_one()
    response.headers["X-Total-Count"] = str(total)

    rows = (await db.execute(
        select(ScanEntry, User.first_name, User.last_name, Membership.plan)
        .outerjoin(QRCard, ScanEntry.qr_card_id == QRCard.id)
        .outerjoin(Membership, QRCard.membership_id == Membership.id)
        .outerjoin(User, Membership.user_id == User.id)
        .order_by(ScanEntry.scanned_at.desc())
        .offset(skip)
        .limit(limit)
    )).all()

    return [
        ScanEntryResponse(
            id=row.ScanEntry.id,
            code=row.ScanEntry.code,
            status=row.ScanEntry.status,
            scanned_at=row.ScanEntry.scanned_at,
            member_name=f"{row.first_name} {row.last_name}" if row.first_name else None,
            plan=row.plan,
        )
        for row in rows
    ]


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

    result = await db.execute(select(User).where(User.id == body.user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    result = await db.execute(
        select(MembershipPlan).where(MembershipPlan.key == body.plan_key, MembershipPlan.type == body.plan_type, MembershipPlan.is_active == True)
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Plan '{body.plan_key}' invalid.")

    start = datetime.combine(body.start_date, datetime.min.time())
    end = compute_end_date(start, plan)

    overlap_check = await db.execute(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.start_date < end,
            Membership.end_date > start,
        )
    )
    if overlap_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Data selectată se suprapune cu un abonament existent.",
        )

    membership = Membership(
        user_id=user.id,
        plan=plan.key,
        status="activ",
        amount=plan.amount,
        start_date=start,
        end_date=end,
    )
    db.add(membership)
    await db.flush()

    card.membership_id = membership.id
    card.is_active = True
    return card


# ── POST /admin/qrcards/{code}/renew ─────────────────────────────────────────
@router.post("/{code}/renew", response_model=QRCardResponse)
async def renew_qr_card(
    code: str,
    body: RenewQRCardRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(QRCard)
        .where(QRCard.code == code)
        .options(selectinload(QRCard.membership).selectinload(Membership.user))
    )
    card = result.scalar_one_or_none()
    if not card:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="QR card not found.")
    if not card.is_active or not card.membership:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Card is not active or has no membership.")
    if card.membership.end_date >= datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Membership is still active.")

    user = card.membership.user

    result = await db.execute(
        select(MembershipPlan).where(
            MembershipPlan.key == body.plan_key,
            MembershipPlan.type == body.plan_type,
            MembershipPlan.is_active == True,
        )
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Plan '{body.plan_key}' invalid.")

    start = datetime.combine(body.start_date, datetime.min.time())
    end = compute_end_date(start, plan)

    overlap_check = await db.execute(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.start_date < end,
            Membership.end_date > start,
        )
    )
    if overlap_check.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Data selectată se suprapune cu un abonament existent.",
        )

    membership = Membership(
        user_id=user.id,
        plan=plan.key,
        status="activ",
        amount=plan.amount,
        start_date=start,
        end_date=end,
    )
    db.add(membership)
    await db.flush()

    card.membership_id = membership.id
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

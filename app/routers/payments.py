import os
import time
import uuid
import hashlib
import base64
from datetime import date, datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.membership import compute_end_date
from app.core.dependencies import require_user
from app.models.membership import Membership
from app.models.membership_plan import MembershipPlan
from app.models.qr_card import QRCard
from app.models.user import User

router = APIRouter()

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")


# ── Request schema ────────────────────────────────────────────────────────────
class CheckoutRequest(BaseModel):
    plan: str
    plan_type: str = "full_time"
    start_date: date = None

    @field_validator("start_date", mode="before")
    @classmethod
    def default_start_date(cls, v):
        if v is None:
            return date.today()
        return v

    @field_validator("start_date")
    @classmethod
    def start_date_not_past(cls, v):
        if v < date.today():
            raise ValueError("Data de start nu poate fi în trecut.")
        return v


def _build_payment_service():
    from netopia_sdk.config import Config
    from netopia_sdk.client import PaymentClient
    from netopia_sdk.payment import PaymentService

    public_key = os.getenv("NETOPIA_PUBLIC_KEY", "").strip()
    if not public_key:
        raise RuntimeError("NETOPIA_PUBLIC_KEY environment variable is not set")

    config = Config(
        api_key=os.getenv("NETOPIA_API_KEY", ""),
        pos_signature=os.getenv("NETOPIA_POS_SIGNATURE", ""),
        is_live=os.getenv("NETOPIA_IS_LIVE", "false").lower() == "true",
        notify_url=f"{BACKEND_URL}/payments/ipn",
        redirect_url=f"{FRONTEND_URL}/success",
        public_key_str=public_key,
        pos_signature_set=[os.getenv("NETOPIA_POS_SIGNATURE", "")],
    )
    return PaymentService(PaymentClient(config))


# ── POST /payments/create-checkout-session ────────────────────────────────────
@router.post("/create-checkout-session")
async def create_checkout_session(
    body: CheckoutRequest,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    plan_result = await db.execute(
        select(MembershipPlan).where(MembershipPlan.key == body.plan, MembershipPlan.type == body.plan_type, MembershipPlan.is_active == True)
    )
    plan = plan_result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=400, detail=f"Plan '{body.plan}' ({body.plan_type}) not found.")

    # Reject if start_date falls within any active membership
    requested_start = datetime.combine(body.start_date, datetime.min.time())
    overlap = await db.execute(
        select(Membership).where(
            Membership.user_id == current_user.id,
            Membership.status == "activ",
            Membership.start_date <= requested_start,
            Membership.end_date > requested_start,
        )
    )
    if overlap.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="Data selectată se suprapune cu un abonament activ.",
        )

    from netopia_sdk.requests.models import (
        StartPaymentRequest, ConfigData, PaymentData, PaymentOptions,
        Instrument, OrderData, BillingData, ShippingData, ProductsData,
    )

    # Encode user_id, plan, and start_date into orderID so the IPN handler can recover them
    start_date_str = body.start_date.strftime("%Y%m%d")
    plan_type_code = "ft" if body.plan_type == "full_time" else "dt"
    order_id = f"GYM-{current_user.id}-{body.plan}-{plan_type_code}-{start_date_str}-{int(time.time())}"

    first_name = current_user.first_name
    last_name = current_user.last_name

    # Netopia expects amounts in RON (not bani)
    amount_ron = plan.amount / 100

    billing = BillingData(
        email=current_user.email,
        phone="",
        firstName=first_name,
        lastName=last_name,
        city="",
        country=642,        # Romania ISO numeric code
        countryName="Romania",
        state="",
        postalCode="",
        details="",
    )

    request_data = StartPaymentRequest(
        config=ConfigData(
            emailTemplate="",
            emailSubject="",
            cancelUrl=f"{FRONTEND_URL}/cancel",
            notifyUrl=f"{BACKEND_URL}/payments/ipn",
            redirectUrl=f"{FRONTEND_URL}/success",
            language="ro",
        ),
        payment=PaymentData(
            options=PaymentOptions(installments=1, bonus=0),
            instrument=Instrument(
                type="card",
                account="",
                expMonth=0,
                expYear=0,
                secretCode="",
                token="",
                clientID="",
            ),
            data={},
        ),
        order=OrderData(
            ntpID=None,
            posSignature=None,
            dateTime=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
            orderID=order_id,
            description=plan.name,
            amount=amount_ron,
            currency="RON",
            billing=billing,
            shipping=ShippingData(
                email=current_user.email,
                phone="",
                firstName=first_name,
                lastName=last_name,
                city="",
                country=642,
                countryName="Romania",
                state="",
                postalCode="",
                details="",
            ),
            products=[ProductsData(
                name=plan.name,
                code=body.plan,
                category="membership",
                price=amount_ron,
                vat=0,
            )],
            installments={},
            data={},
        ),
    )

    try:
        payment_service = _build_payment_service()
        response = payment_service.start_payment(request_data)
        return {"url": response.payment["paymentURL"]}
    except Exception as e:
        print(f"Netopia error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


# ── POST /payments/ipn ────────────────────────────────────────────────────────
@router.post("/ipn")
async def netopia_ipn(request: Request, db: AsyncSession = Depends(get_db)):
    import json as _json
    payload = await request.body()

    # Parse the raw payload first — orderID and status live here
    try:
        ipn_data = _json.loads(payload.decode("utf-8"))
    except Exception:
        return JSONResponse({"errorCode": 1})

    order_id: str = ipn_data.get("order", {}).get("orderID", "")
    # Status int: 3=PAID, 5=CONFIRMED (see PaymentStatus constants)
    status: int = ipn_data.get("payment", {}).get("status", 0)

    # Verify IPN token.
    # The `sub` claim is a base64-encoded SHA-512 hash of the raw body, which
    # proves the payload hasn't been tampered with. We also check issuer and
    # audience. Full RSA signature verification requires Netopia's server signing
    # key (distinct from the per-merchant POS certificate); use that key in
    # NETOPIA_PUBLIC_KEY for production once obtained from Netopia support.
    verification_token = request.headers.get("Verification-token", "")
    if not verification_token:
        print("⚠️  IPN missing Verification-token header")
        return JSONResponse({"errorCode": 1})

    try:
        def _pad(s): return s + "=" * ((4 - len(s) % 4) % 4)
        parts = verification_token.split(".")
        claims = _json.loads(base64.urlsafe_b64decode(_pad(parts[1])))

        pos_sig = os.getenv("NETOPIA_POS_SIGNATURE", "")
        iss = claims.get("iss")
        aud = claims.get("aud", [])
        aud_val = aud[0] if isinstance(aud, list) else aud
        sub = claims.get("sub", "")

        if iss != "NETOPIA Payments":
            raise ValueError(f"Invalid issuer: {iss!r}")
        if aud_val != pos_sig:
            raise ValueError(f"Audience {aud_val!r} does not match POS signature")

        expected_hash = base64.b64encode(
            hashlib.sha512(payload).digest()
        ).decode("utf-8")
        if expected_hash != sub:
            raise ValueError("Payload hash mismatch")

        print("✅ IPN token verified (issuer + audience + payload hash)")
    except Exception as e:
        print(f"⚠️  IPN verification failed: {e}")
        return JSONResponse({"errorCode": 1})

    print(f"Netopia IPN — orderID={order_id!r} status={status}")

    # Only activate membership on paid/confirmed
    if status not in (3, 5):
        return JSONResponse({"errorCode": 0})

    if not order_id or not order_id.startswith("GYM-"):
        return JSONResponse({"errorCode": 0})

    # Decode: GYM-{user_id}-{plan}-{plan_type_code}-{start_date}-{timestamp}
    parts = order_id.split("-")
    if len(parts) < 6:
        return JSONResponse({"errorCode": 0})

    try:
        user_id = int(parts[1])
        plan_key = parts[2]
        plan_type_code = parts[3]
        start_date_str = parts[4]
        start_date = datetime.strptime(start_date_str, "%Y%m%d")
    except (ValueError, IndexError):
        return JSONResponse({"errorCode": 0})

    plan_type = "full_time" if plan_type_code == "ft" else "day_time"
    plan_result = await db.execute(select(MembershipPlan).where(MembershipPlan.key == plan_key, MembershipPlan.type == plan_type))
    plan = plan_result.scalar_one_or_none()
    if not plan:
        print(f"⚠️ Unknown plan '{plan_key}' in orderID: {order_id}")
        return JSONResponse({"errorCode": 0})

    result_db = await db.execute(select(User).where(User.id == user_id))
    user = result_db.scalar_one_or_none()
    if not user:
        print(f"⚠️ No user found for id: {user_id}")
        return JSONResponse({"errorCode": 0})

    # Guard against duplicate IPNs for the same order (Netopia sends PAID then CONFIRMED)
    existing = await db.execute(
        select(Membership).where(Membership.payment_session_id == order_id)
    )
    if existing.scalar_one_or_none():
        print(f"ℹ️  Membership already exists for order {order_id}, skipping.")
        return JSONResponse({"errorCode": 0})

    start = start_date
    end = compute_end_date(start, plan)

    membership = Membership(
        user_id=user.id,
        plan=plan_key,
        status="activ",
        amount=plan.amount,
        start_date=start,
        end_date=end,
        payment_session_id=order_id,
    )
    db.add(membership)
    await db.flush()  # populate membership.id

    qr_code = f"CARD_{uuid.uuid4().hex[:12].upper()}"
    qr_card = QRCard(
        code=qr_code,
        type="digital",
        is_active=True,
        membership_id=membership.id,
    )
    db.add(qr_card)
    print(f"✅ Membership created — user: {user.email}, plan: {plan_key}, ends: {end.date()}")

    return JSONResponse({"errorCode": 0})

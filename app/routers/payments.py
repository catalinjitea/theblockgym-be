import os
import stripe
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.membership import Membership
from app.models.user import User

router = APIRouter()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

# ── Plan definitions ──────────────────────────────────────────────────────────
PLANS = {
    "lunar": {
        "name": "Abonament Lunar",
        "amount": 22000,        # 220 RON in bani
        "currency": "ron",
        "description": "Acces complet sală",
        "duration_days": 30,
    },
    "3luni": {
        "name": "Abonament 3 Luni",
        "amount": 58500,        # 585 RON in bani
        "currency": "ron",
        "description": "Acces complet sală · Economisești 75 RON",
        "duration_days": 90,
    },
    "6luni": {
        "name": "Abonament 6 Luni",
        "amount": 108000,       # 1080 RON in bani
        "currency": "ron",
        "description": "Acces complet sală · Economisești 240 RON",
        "duration_days": 180,
    },
    "anual": {
        "name": "Abonament Anual",
        "amount": 204000,       # 2040 RON in bani
        "currency": "ron",
        "description": "Acces nelimitat · Economisești 600 RON",
        "duration_days": 365,
    },
}


# ── Request schema ────────────────────────────────────────────────────────────
class CheckoutRequest(BaseModel):
    plan: str


# # ── POST /payments/create-checkout-session ────────────────────────────────────
# @router.post("/create-checkout-session")
# async def create_checkout_session(body: CheckoutRequest):
#     plan = PLANS.get(body.plan)
#     if not plan:
#         raise HTTPException(status_code=400, detail=f"Plan '{body.plan}' not found.")

#     try:
#         session = stripe.checkout.Session.create(
#             payment_method_types=["card"],
#             line_items=[
#                 {
#                     "price_data": {
#                         "currency": plan["currency"],
#                         "unit_amount": plan["amount"],
#                         "product_data": {
#                             "name": plan["name"],
#                             "description": plan["description"],
#                         },
#                     },
#                     "quantity": 1,
#                 }
#             ],
#             mode="payment",
#             success_url=f"{FRONTEND_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
#             cancel_url=f"{FRONTEND_URL}/cancel",
#             metadata={"plan": body.plan},
#         )
#         return {"url": session.url}

#     except stripe.error.StripeError as e:
#         print(f"Stripe error: {e}")
#         raise HTTPException(status_code=400, detail=str(e))


# # ── POST /payments/webhook ────────────────────────────────────────────────────
# @router.post("/webhook")
# async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
#     payload = await request.body()
#     sig_header = request.headers.get("stripe-signature")

#     try:
#         event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
#     except stripe.error.SignatureVerificationError:
#         raise HTTPException(status_code=400, detail="Invalid webhook signature.")

#     if event["type"] == "checkout.session.completed":
#         session = event["data"]["object"]
#         plan_key = session.get("metadata", {}).get("plan")
#         customer_email = session.get("customer_details", {}).get("email")
#         stripe_session_id = session.get("id")

#         plan = PLANS.get(plan_key)
#         if not plan or not customer_email:
#             return JSONResponse({"status": "ignored"})

#         # Find user by email
#         result = await db.execute(select(User).where(User.email == customer_email))
#         user = result.scalar_one_or_none()
#         if not user:
#             print(f"⚠️ No user found for email: {customer_email}")
#             return JSONResponse({"status": "user not found"})

#         # Create membership
#         start = datetime.utcnow()
#         end = start + timedelta(days=plan["duration_days"])

#         membership = Membership(
#             user_id=user.id,
#             plan=plan_key,
#             status="activ",
#             amount=plan["amount"],
#             start_date=start,
#             end_date=end,
#             stripe_session_id=stripe_session_id,
#         )
#         db.add(membership)
#         print(f"✅ Membership created — user: {customer_email}, plan: {plan_key}, ends: {end.date()}")

#     return JSONResponse({"status": "ok"})

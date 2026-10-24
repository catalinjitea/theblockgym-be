import asyncio
import contextlib
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core import notification_jobs
from app.core.database import Base, engine
from app.models import membership_plan, promo_code, scan_entry, session, booking, refresh_token, user_preferences, push_device, notification_log  # noqa: F401 – registers models with Base
from app.routers import admin, auth, me, memberships, mobile_auth, payments, plans, promo, qr_cards, sessions

app = FastAPI(title="The Block Gym API")

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        os.getenv("FRONTEND_URL", "http://localhost:3000"),
        "https://www.theblockgym.ro",
        "https://theblockgym.ro",
        "https://theblockgym-fe.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)

# ── Create DB tables on startup ───────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    if notification_jobs.JOBS_ENABLED:
        app.state.notification_task = asyncio.create_task(notification_jobs.notification_loop())


@app.on_event("shutdown")
async def shutdown():
    task = getattr(app.state, "notification_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router,        prefix="/auth",        tags=["Auth"])
app.include_router(admin.router,       prefix="/admin",       tags=["Admin"])
app.include_router(memberships.router, prefix="/memberships", tags=["Memberships"])
app.include_router(payments.router,    prefix="/payments",    tags=["Payments"])
app.include_router(plans.router,       prefix="/plans",        tags=["Plans"])
app.include_router(promo.router,       prefix="/promo",        tags=["Promo"])
app.include_router(qr_cards.router,    prefix="/admin/qrcards", tags=["QR Cards"])
app.include_router(sessions.router,    prefix="/sessions",      tags=["Sessions"])

# ── v1: the mobile app's API ──────────────────────────────────────────────────
# The same member-facing handlers, mounted again under /v1 so the app depends
# on a versioned contract. The unversioned routes above stay as they are for
# the website. No breaking changes under /v1 once the app ships.
app.include_router(mobile_auth.router, prefix="/v1/auth",        tags=["v1 Auth"])
app.include_router(me.router,          prefix="/v1/me",          tags=["v1 Me"])
app.include_router(memberships.router, prefix="/v1/memberships", tags=["v1 Memberships"])
app.include_router(plans.router,       prefix="/v1/plans",       tags=["v1 Plans"])
app.include_router(sessions.router,    prefix="/v1/sessions",    tags=["v1 Sessions"])

# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok"}

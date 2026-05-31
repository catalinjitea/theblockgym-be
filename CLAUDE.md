# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the dev server
uvicorn app.main:app --reload --port 8000

# Apply all pending migrations
python -m alembic upgrade head

# Create a new migration after editing a model
python -m alembic revision --autogenerate -m "describe_your_change"

# Roll back one migration
python -m alembic downgrade -1

# Check migration state
python -m alembic current
python -m alembic history
```

API docs available at http://localhost:8000/docs when the server is running.

There is no test suite in this project.

## Architecture

**Stack:** FastAPI + async SQLAlchemy 2.0 + PostgreSQL (asyncpg driver) + Alembic. Everything is async (`AsyncSession`, `async def` route handlers). The app entrypoint is [app/main.py](app/main.py).

**Layer structure:**
- `app/models/` — SQLAlchemy ORM models (declare schema, relationships)
- `app/schemas/` — Pydantic models for request validation and response serialization
- `app/routers/` — FastAPI route handlers, one file per domain
- `app/core/` — shared utilities: DB session (`database.py`), JWT auth (`security.py`), route dependencies (`dependencies.py`), email (`email.py`), membership date math (`membership.py`), WebSocket manager (`websocket.py`)

**DB session pattern:** `get_db()` in [app/core/database.py](app/core/database.py) is a FastAPI dependency that yields an `AsyncSession`, auto-commits on success and rolls back on exception. Always use `await db.flush()` to get generated IDs mid-transaction before the commit that `get_db` handles.

**Auth:** JWT stored in an httponly cookie named `access_token`. `get_current_user` reads and validates it. `require_user` and `require_admin` are composed on top. Admin tokens have a longer expiry (24h default vs 60min for users).

**Router registration:** All routers are registered in [app/main.py](app/main.py). The `qr_cards` router is mounted at `/admin/qrcards` (not `/qrcards`).

## Domain: QR Cards & Entry

There are two card types: `physical` (pre-printed cards) and `digital` (auto-created on membership activation).

`POST /admin/qrcards/verify/{code}` — the scan endpoint in [app/routers/qr_cards.py](app/routers/qr_cards.py). No auth required (used by physical scanner). On a valid scan it auto-repoints the card to an advance-purchased membership if the current one just expired. Results are broadcast over WebSocket to any connected admin dashboards.

WebSocket: `WS /admin/qrcards/ws` — the `ConnectionManager` singleton in [app/core/websocket.py](app/core/websocket.py) broadcasts every verify result as JSON to all connected clients.

QR cards are created/reused in three places: IPN handler (`payments.py`), `assign_membership` (`admin.py`), and `activate_qr_card` (`qr_cards.py`). The pattern is: reuse the user's most recent digital card if one exists and re-point it to the new membership; otherwise create a new one.

## Domain: Memberships & Plans

`MembershipPlan` stores named plans with a `key` (e.g. `"lunar"`), `type` (`full_time` / `day_time`), `amount` in bani (integer), and either `duration_days` or `duration_months`. `compute_end_date` in [app/core/membership.py](app/core/membership.py) handles month-boundary arithmetic.

Overlap checks are enforced before creating any membership. The query pattern is: `start_date < new_end AND end_date > new_start`.

## Domain: Payments

Netopia integration in [app/routers/payments.py](app/routers/payments.py). The `orderID` encodes `user_id`, `plan`, `plan_type`, and `start_date` so the IPN handler can reconstruct context without a pending-order table. IPN verification checks issuer, audience (POS signature), and SHA-512 payload hash from the JWT `sub` claim. Duplicate IPN calls are guarded by checking `payment_session_id` uniqueness on `Membership`.

## Environment

Copy `env.example` to `.env`. Key variables: `DATABASE_URL` (Railway may send `postgres://` — the DB module normalizes it to `postgresql+asyncpg://`), `JWT_SECRET_KEY`, `NETOPIA_*`, `FRONTEND_URL`, `BACKEND_URL`, `ENVIRONMENT` (set to `production` to enable secure/samesite-none cookies).

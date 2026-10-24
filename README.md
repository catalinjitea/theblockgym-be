# The Block Gym — Backend API

## Folder structure

```
theblockgym-be/
  app/
    main.py                  ← FastAPI app entry point
    core/                    ← DB engine, auth, dependencies
    models/                  ← SQLAlchemy ORM models
    routers/                 ← API route handlers
    schemas/                 ← Pydantic request/response schemas
  alembic/
    env.py                   ← Alembic async configuration
    versions/                ← Migration scripts
  alembic.ini                ← Alembic config
  requirements.txt
  env.example                ← copy to .env and fill in secrets
```

---

## 1. Netopia Payments setup

1. Create a merchant account at [admin.netopia-payments.com](https://admin.netopia-payments.com)
2. Go to your POS settings and copy:
   - **API Key**
   - **POS Signature**
   - **Public Key** (RSA PEM)
3. Register your IPN (webhook) URL: `https://yourdomain.com/payments/ipn`
4. Set `NETOPIA_IS_LIVE=false` for sandbox, `true` for production

---

## 2. Backend setup

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp env.example .env
# Edit .env and fill in your credentials
```

Start the server:
```bash
uvicorn app.main:app --reload --port 8000
```

API docs available at: http://localhost:8000/docs

---

## 3. Environment variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `NETOPIA_API_KEY` | API key from Netopia admin |
| `NETOPIA_POS_SIGNATURE` | POS signature from Netopia admin |
| `NETOPIA_IS_LIVE` | `false` for sandbox, `true` for production |
| `NETOPIA_PUBLIC_KEY` | RSA public key PEM string from Netopia |
| `FRONTEND_URL` | Your frontend URL (used for redirect after payment) |
| `BACKEND_URL` | Your backend URL (used to build the IPN callback URL) |
| `JWT_SECRET_KEY` | JWT signing secret (website cookie and mobile tokens) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Website login lifetime, default `60` |
| `MOBILE_ACCESS_TOKEN_EXPIRE_MINUTES` | Mobile app access token lifetime, default `15` |
| `REFRESH_TOKEN_EXPIRE_DAYS` | Mobile app refresh token lifetime, default `60` (renewed on every refresh) |
| `NOTIFICATION_JOBS_ENABLED` | `false` stops the push notification jobs in this process (default `true`). Keep them on in exactly one process. |
| `NOTIFICATION_JOBS_INTERVAL_SECONDS` | How often the jobs run, default `300` |
| `EXPO_ACCESS_TOKEN` | Only if "Enhanced push security" is enabled for the Expo project |

---

## 4. Payment flow

```
User clicks "Cumpără" (must be logged in)
  → POST /payments/create-checkout-session  { "plan": "lunar" }
  → Backend calls Netopia StartPayment API
  → Returns { "url": "https://secure.netopia-payments.com/..." }
  → Frontend redirects user to that URL
  → User completes payment on Netopia hosted page
  → Netopia sends IPN → POST /payments/ipn  (FastAPI)
  → Backend verifies IPN signature
  → Membership activated in DB
  → User redirected to /success
```


## 5. Database migrations (Alembic)

This project uses [Alembic](https://alembic.sqlalchemy.org) for schema migrations with async SQLAlchemy.

### Apply all pending migrations

```bash
python -m alembic upgrade head
```

Run this on every deployment before starting the server.

### Check current migration state

```bash
python -m alembic current    # shows the revision applied to the DB
python -m alembic history    # shows the full migration chain
```

### Create a new migration after editing a model

```bash
# Autogenerate detects the diff between models and the live DB
python -m alembic revision --autogenerate -m "describe_your_change"

# Review the generated file in alembic/versions/, then apply it
python -m alembic upgrade head
```

### Roll back the last migration

```bash
python -m alembic downgrade -1
```

### Roll back to a specific revision

```bash
python -m alembic downgrade <revision_id>
```

> **Note:** Alembic reads `DATABASE_URL` from the environment (or `.env`). Make sure it is set before running any `alembic` command.

---

## 6. Mobile app API (`/v1`)

The mobile app (`theblockgym-app`) uses only routes under `/v1`, a versioned copy of the member-facing routes. The unversioned routes stay as they are for the website. **No breaking changes under `/v1` once the app ships**: add fields and endpoints, don't rename or remove them.

| Route | Purpose |
|---|---|
| `POST /v1/auth/token` | `{email, password}` → `{access_token, refresh_token, token_type, expires_in}` |
| `POST /v1/auth/refresh` | `{refresh_token}` → a new pair. The old refresh token stops working; presenting it again logs out that whole login. |
| `POST /v1/auth/logout` | `{refresh_token, expo_push_token?}` → 204, revokes that login and unregisters the phone from push |
| `GET /v1/auth/me` | Current user |
| `GET/PATCH /v1/me/preferences` | `{language: "ro" \| "en" \| null, class_reminders: bool, expiry_alerts: bool}`. Language is `null` until saved; both toggles default to on. Stored in `user_preferences`. |
| `POST /v1/me/devices` | `{expo_push_token, platform: "ios" \| "android", locale?}` → 204. Idempotent; a token already linked to another member moves to this one. |
| `DELETE /v1/me/devices/{token}` | 204; only the member's own device |
| `/v1/memberships/*`, `/v1/plans`, `/v1/sessions/*` | Same handlers as the unversioned routes |

Authenticated requests send `Authorization: Bearer <access_token>`. Every route that accepts the website's cookie also accepts a Bearer token; when both are present the header wins.

Refresh tokens are stored hashed in `refresh_tokens` (one row per issued token, grouped by `family_id` per login).

### OpenAPI spec

`docs/openapi.json` is the contract the app generates its types from. After changing any route or schema, regenerate and commit it:

```bash
python scripts/export_openapi.py
```

### Push notifications

A background loop started with the app (`app/core/notification_jobs.py`) runs every 5 minutes and sends through Expo's push service:

- **Class reminder** for every confirmed booking whose class starts within the next hour.
- **Expiry alerts** 3 days before a membership's last day and on the last day, between 10:00 and 21:00 Romanian time, skipped when the member already has a later membership.

Both respect the member's toggles and language (`user_preferences`). Every notification is recorded in `notification_log` before it is sent, so nothing is sent twice, even if two processes run the jobs. Tokens Expo reports as no longer registered are deleted.


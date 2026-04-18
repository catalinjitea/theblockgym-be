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
| `SECRET_KEY` | JWT signing secret |

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

# The Block Gym — Stripe + FastAPI Integration

## Folder structure

```
backend/
  main.py              ← FastAPI app
  requirements.txt
  .env.example         ← copy to .env and fill in secrets

frontend/
  app/
    checkout/page.tsx  ← replaces /checkout route
    success/page.tsx   ← shown after successful payment
    cancel/page.tsx    ← shown when user cancels
  .env.local.example   ← copy to .env.local and fill in
```

---

## 1. Stripe setup

1. Create a free account at https://stripe.com
2. Go to **Developers → API keys** and copy your **Secret key** (`sk_test_…`)
3. For the webhook (step 4), you'll also need the **Webhook signing secret** (`whsec_…`)

---

## 2. Backend setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env and add your STRIPE_SECRET_KEY
```

Start the server:
```bash
uvicorn main:app --reload --port 8000
```

API docs available at: http://localhost:8000/docs

---

## 3. Frontend setup

```bash
cd your-nextjs-project
cp .env.local.example .env.local
# .env.local already has: NEXT_PUBLIC_API_URL=http://localhost:8000
```

Copy the three page files into your Next.js `app/` directory:
- `frontend/app/checkout/page.tsx`  → `app/checkout/page.tsx`
- `frontend/app/success/page.tsx`   → `app/success/page.tsx`
- `frontend/app/cancel/page.tsx`    → `app/cancel/page.tsx`

The existing `page.tsx` (homepage) **does not need changes** — the "Cumpără"
buttons already link to `/checkout?plan=lunar` etc.

---

## 4. Stripe webhook (for production / local testing)

Webhooks let Stripe notify your backend when a payment completes so you can
activate the membership in your database.

**Local testing with Stripe CLI:**
```bash
# Install: https://stripe.com/docs/stripe-cli
stripe login
stripe listen --forward-to localhost:8000/webhook
# Copy the whsec_… secret printed and add it to .env as STRIPE_WEBHOOK_SECRET
```

**Production:**
1. Dashboard → Developers → Webhooks → Add endpoint
2. URL: `https://yourdomain.com/webhook`
3. Event to listen for: `checkout.session.completed`
4. Copy the signing secret to your production `.env`

---

## 5. Activating memberships

In `main.py`, find this comment inside the webhook handler and add your logic:

```python
# TODO: activate membership in your database here
```

Example with a hypothetical DB call:
```python
await db.memberships.create(email=customer_email, plan=plan)
```

---

## Environment variables summary

| Variable | Where | Description |
|---|---|---|
| `STRIPE_SECRET_KEY` | backend `.env` | Stripe secret key (`sk_test_…`) |
| `STRIPE_WEBHOOK_SECRET` | backend `.env` | Webhook signing secret (`whsec_…`) |
| `FRONTEND_URL` | backend `.env` | Your Next.js URL (for redirects) |
| `NEXT_PUBLIC_API_URL` | frontend `.env.local` | Your FastAPI URL |

---

## Payment flow

```
User clicks "Cumpără"
  → /checkout?plan=lunar
  → POST /create-checkout-session  (FastAPI)
  → Stripe Checkout hosted page
  → Payment success
  → Redirect to /success
  → Stripe sends webhook → POST /webhook  (FastAPI)
  → Membership activated in your DB
```

# Link memberships to the sold plan (`plan_id` FK) and snapshot the session quota

## Context

Group-class quotas (`sessions_count`) live on `membership_plans` and are resolved **live and heuristically** at every read: memberships only store the plan `key` (not unique — the constraint is `(key, type)`), so `_pick_plan` ([memberships.py:41-46](app/routers/memberships.py#L41-L46)) guesses the row by matching `amount`, falling back to an arbitrary first row. The booking gate ([sessions.py:40-48](app/routers/sessions.py#L40-L48)) does its own key-based lookup. Editing a plan row would retroactively change quotas on already-sold memberships.

Fix, two complementary parts:

- **`plan_id` FK** — identity: a membership permanently records *which* plan row it was sold under. Kills the key+amount heuristic for new rows, gives exact `type`/`name`, and its `RESTRICT` behavior enforces deactivate-don't-delete (changed terms = new plan row + `is_active = false` on the old, the existing hand-managed-SQL practice; the plans API is read-only).
- **`sessions_allowance` snapshot** — terms: the quota copied onto the membership at purchase, same pattern and rationale as the existing `freeze_days_allowance` columns ([membership.py:21-25](app/models/membership.py#L21-L25)). Quota checks read the membership row only, so even a manual in-place `UPDATE` of the plan can never move a sold membership's quota.

The duplication between the two is deliberate: FK = identity, snapshot = immutable terms — mirroring how `amount` and the freeze allowances are already snapshotted despite the plan holding the same data.

## Changes

### 1. Model — [app/models/membership.py](app/models/membership.py)

```python
plan_id:            Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("membership_plans.id"), nullable=True, index=True)
# next to the freeze snapshot block, same rationale/comment style:
sessions_allowance: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
...
plan_ref = relationship("MembershipPlan")
```

Both nullable: pre-existing memberships keep `NULL` and fall back to today's behavior. (`plan_ref` because the attribute name `plan` is taken by the key column. `sessions_allowance` follows the plan→membership renaming convention set by `max_freeze_days` → `freeze_days_allowance`.)

### 2. Snapshot helper — [app/core/membership.py](app/core/membership.py)

Rename `snapshot_freeze_allowance` → `snapshot_plan_entitlements` (it's a helper function splatted into `Membership(...)`, not a stored field) and extend:

```python
return {
    "plan_id": plan.id,
    "freeze_days_allowance": plan.max_freeze_days,
    "freezes_allowance": plan.max_freezes,
    "sessions_allowance": plan.sessions_count,
}
```

Update its docstring. All four creation sites splat this helper, so they pick everything up with only an import/name change:
- [payments.py:322](app/routers/payments.py#L322) (IPN handler)
- [admin.py:484](app/routers/admin.py#L484) (`assign_membership`)
- [qr_cards.py:313](app/routers/qr_cards.py#L313) and [qr_cards.py:380](app/routers/qr_cards.py#L380)

### 3. Shared used-count helper — [app/core/membership.py](app/core/membership.py)

Extract the duplicated "confirmed bookings for sessions inside the membership window" query ([sessions.py:65-74](app/routers/sessions.py#L65-L74) verbatim) into:

```python
async def count_sessions_used(db: AsyncSession, user_id: int, membership: Membership) -> int:
```

### 4. Booking gate — `_group_classes_booking_error` in [app/routers/sessions.py](app/routers/sessions.py)

Load memberships with `.options(selectinload(Membership.plan_ref))` and drop the key-based `MembershipPlan` query. Group access comes from the FK, the quota from the snapshot:

```python
group_memberships = [m for m in memberships
                     if m.plan_ref and m.plan_ref.type == "group_classes"]
has_plan = bool(group_memberships)
for membership in group_memberships:
    if not (membership.start_date <= session.start_datetime <= membership.end_date):
        continue
    covers_session = True
    if membership.sessions_allowance is None:
        return None                      # unlimited
    used = await count_sessions_used(db, user.id, membership)
    if used < membership.sessions_allowance:
        return None
```

Legacy rows (`plan_id` NULL) are treated as non-group — correct, since no group memberships exist yet. Error precedence (`no_group_access` / `not_covered` / `quota_exhausted`) unchanged.

### 5. Display — [app/routers/memberships.py](app/routers/memberships.py)

- `get_my_membership`: load `plan_ref` via `selectinload`. When set, use `plan_ref.name` for `plan_name`, and the quota block becomes: if `plan_ref.type == "group_classes"` and `membership.sessions_allowance is not None` → `sessions_total = membership.sessions_allowance`, `sessions_remaining = max(0, sessions_allowance - await count_sessions_used(...))`. When `plan_id` is NULL, fall back to the existing `_resolve_plan` heuristic for `plan_name` only.
- `get_my_membership_history`: prefer `plan_ref.name`, `_pick_plan` fallback for legacy rows. `_plans_by_key`/`_pick_plan` stay, serving only legacy rows.

Response shape (`sessions_total` / `sessions_remaining` / `plan_name`) is unchanged → **no frontend changes**.

### 6. Migration (Alembic autogenerate, then hand-add the `plan_id` backfill)

`python -m alembic revision --autogenerate -m "add_plan_id_and_sessions_allowance_to_memberships"`

`add_column` × 2 + FK + index, then backfill existing memberships in `upgrade()` via `op.execute`:

```sql
UPDATE memberships m
SET plan_id = p.id, sessions_allowance = p.sessions_count
FROM membership_plans p
WHERE p.key = m.plan AND p.amount = m.amount;
```

**Exact `key + amount` match only** — no arbitrary key-only fallback: where keys collide across plan types, permanently linking a guessed row is worse than leaving `plan_id` NULL, which keeps today's display behavior via the `_resolve_plan` fallback. (No group memberships exist yet, so `sessions_allowance` backfills to NULL everywhere — included for uniformity.) `downgrade()` drops both columns.

Post-migration coverage check — SQL for the user to run:

```sql
SELECT plan_id IS NULL AS unlinked, count(*) FROM memberships GROUP BY 1;
SELECT DISTINCT plan, amount FROM memberships WHERE plan_id IS NULL;  -- inspect any leftovers
```

## Out of scope

- The cross-session quota race and the frozen-membership booking gap (separate findings; `count_sessions_used` centralizes the logic so both become one-place fixes later).
- Migrating `plan_name`/freeze display or legacy heuristics beyond the fallback described above.

## Verification

No test suite — verify manually against the dev DB:

1. `python -m alembic upgrade head`, then `uvicorn app.main:app --reload --port 8000`.
2. Assign a group-classes membership via the admin endpoint; as that user, `GET /memberships/me` shows `plan_name` + `sessions_total`/`sessions_remaining`; `POST/DELETE /sessions/{id}/book` enforces the quota and cancelling refunds it.
3. The point of the change: change the plan's `sessions_count` (SQL for the user — both an in-place `UPDATE` and the retire-and-replace flow). Existing member's `/memberships/me` and booking quota must **not** move in either case; a fresh membership on the new row gets the new quota. `DELETE` of a referenced plan row fails on the FK, as intended.
4. Regression: a pre-migration gym membership (`plan_id` NULL) still returns `plan_name` and freeze fields on `/memberships/me`, with sessions fields null, and gets `no_group_access` when booking.
5. `python -m alembic downgrade -1` then `upgrade head` round-trips cleanly.

-- ============================================================================
-- Freeze allowance seed + backfill
--
-- Run AFTER migration d7a4c81b96e3 and the step-1 deploy (both done),
-- but BEFORE deploying step 2, which starts enforcing these caps.
--
-- Right now every allowance is NULL, and NULL reads as "freezing not
-- permitted" — so step 2 must not ship until this has run.
--
-- Sections 0, 5 and 6 are read-only. Sections 1-4 mutate and are wrapped in a
-- transaction: check the row counts, then COMMIT or ROLLBACK.
--
-- Timestamps: the app writes naive UTC (datetime.utcnow) into plain `timestamp`
-- columns, so comparisons use `now() AT TIME ZONE 'UTC'` rather than bare
-- now(), which Postgres would interpret in the server's local zone.
-- ============================================================================


-- ── 0. REVIEW FIRST (read-only) ─────────────────────────────────────────────

-- 0a. The plan catalogue. Check these tiers before trusting the CASE in
--     section 1 — adjust it to match how you actually sell memberships.
SELECT id, key, type, name, duration_months, duration_days,
       max_freeze_days, max_freezes, is_active
FROM membership_plans
ORDER BY key, type;

-- 0b. Do full_time/day_time rows of the same key disagree on max_freeze_days?
--     Each row here is a key whose allowance can't be resolved exactly, because
--     memberships.plan stores only the key, not the type. Section 2 takes MAX(),
--     i.e. the more generous value. A couple of rows is fine to accept; a lot
--     means those memberships are better settled by hand.
--     COALESCE so that NULL-vs-30 counts as a disagreement.
SELECT key,
       COUNT(DISTINCT COALESCE(max_freeze_days, -1)) AS distinct_limits,
       array_agg(type || '=' || COALESCE(max_freeze_days::text, 'NULL')
                 ORDER BY type)                      AS variants
FROM membership_plans
GROUP BY key
HAVING COUNT(DISTINCT COALESCE(max_freeze_days, -1)) > 1;

-- 0c. Live memberships whose plan key no longer exists in the catalogue.
--     These end up with a NULL allowance and silently lose the ability to
--     freeze. Either recreate the plan row or set their allowance by hand.
SELECT m.plan, COUNT(*) AS live_memberships
FROM memberships m
LEFT JOIN membership_plans p ON p.key = m.plan
WHERE p.key IS NULL
  AND m.end_date >= now() AT TIME ZONE 'UTC'
GROUP BY m.plan;


BEGIN;

-- ── 1. SEED membership_plans.max_freezes ────────────────────────────────────
-- Longer memberships get more separate freezes. A plan that can't be frozen at
-- all (max_freeze_days IS NULL) keeps max_freezes NULL, so the two columns
-- never disagree about whether freezing is permitted.
--
-- Keyed on duration alone, so the retired promo variants (3luni-vara26,
-- 6luni-vara26) pick up the same counts as their standard equivalents.
--
-- Day-based plans have a NULL duration_months, so every comparison below is
-- NULL for them and they fall through to ELSE 1.

UPDATE membership_plans
SET max_freezes = CASE
        WHEN max_freeze_days IS NULL THEN NULL
        WHEN duration_months >= 12   THEN 8
        WHEN duration_months >= 6    THEN 4
        WHEN duration_months >= 3    THEN 2
        ELSE 1
    END;


-- ── 2. BACKFILL membership allowances ───────────────────────────────────────
-- Snapshot the plan entitlement onto every existing membership. This is the
-- last place we resolve a plan by key alone, aggregating across types, because
-- historical rows don't record which type was purchased. Step 2 snapshots the
-- exact plan row at creation instead.

UPDATE memberships m
SET freeze_days_allowance = p.max_freeze_days,
    freezes_allowance     = p.max_freezes
FROM (
    SELECT key,
           MAX(max_freeze_days) AS max_freeze_days,
           MAX(max_freezes)     AS max_freezes
    FROM membership_plans
    GROUP BY key
) p
WHERE m.plan = p.key;


-- ── 3. BACKFILL usage — conservative ────────────────────────────────────────
-- Only the most recent freeze window survives on each row: re-freezing
-- overwrote freeze_start/freeze_end in place, so earlier freezes are not
-- recoverable from this table. We charge that one window and nothing more.
--
-- Restricted to live memberships — expired ones can't be frozen again, so
-- their counters stay at 0.
--
-- Day count is EXCLUSIVE (no +1), mirroring exactly what the old code charged
-- and extended end_date by. Step 2 counts new freezes inclusively, but billing
-- historical rows inclusively would charge a day the member was never
-- compensated for. A backfill should never over-charge.
--
-- Cancelled freezes fall out correctly: unfreeze set freeze_end = now(), so the
-- subtraction gives the days actually spent frozen. A freeze cancelled before
-- it started has freeze_end <= freeze_start and costs nothing.

UPDATE memberships
SET freeze_days_used = GREATEST(0, (freeze_end::date - freeze_start::date)),
    freezes_used     = CASE WHEN freeze_end > freeze_start THEN 1 ELSE 0 END
WHERE freeze_start IS NOT NULL
  AND freeze_end   IS NOT NULL
  AND end_date >= now() AT TIME ZONE 'UTC';


-- ── 4. CLAMP ────────────────────────────────────────────────────────────────
-- Nobody should start out with a negative remainder. This matters where a
-- plan's max_freeze_days was lowered after the membership was sold.

UPDATE memberships
SET freeze_days_used = LEAST(freeze_days_used, freeze_days_allowance)
WHERE freeze_days_allowance IS NOT NULL
  AND freeze_days_used > freeze_days_allowance;

UPDATE memberships
SET freezes_used = LEAST(freezes_used, freezes_allowance)
WHERE freezes_allowance IS NOT NULL
  AND freezes_used > freezes_allowance;

-- Check the row counts above, then:
COMMIT;
-- ROLLBACK;


-- ── 5. REVIEW LIST: probable repeat-freezers (read-only) ────────────────────
-- Section 3 credits each membership with one freeze window. Anyone who
-- exploited the re-freeze bug took more, and the only remaining trace is
-- end_date drifting past what the plan duration implies.
--
-- DO NOT auto-apply this. Drift has two legitimate sources:
--   * PATCH /admin/memberships/{id} sets end_date to whatever an admin typed
--   * a plan's duration may have changed since the membership was sold
--
-- Treat it as a shortlist to settle by hand. The tail is noise; the head is
-- where real abuse shows up.

WITH expected AS (
    SELECT m.id,
           m.user_id,
           m.plan,
           m.start_date,
           m.end_date,
           m.freeze_days_used,
           m.freeze_days_allowance,
           CASE
               WHEN p.duration_months IS NOT NULL
                   THEN m.start_date + (p.duration_months || ' months')::interval - interval '1 second'
               ELSE     m.start_date + (p.duration_days   || ' days')::interval   - interval '1 second'
           END AS expected_end
    FROM memberships m
    JOIN (
        SELECT key,
               MAX(duration_months) AS duration_months,
               MAX(duration_days)   AS duration_days
        FROM membership_plans
        GROUP BY key
    ) p ON p.key = m.plan
    WHERE m.end_date >= now() AT TIME ZONE 'UTC'
)
SELECT e.id                                                          AS membership_id,
       u.email,
       e.plan,
       e.start_date::date,
       e.end_date::date,
       e.expected_end::date,
       (e.end_date::date - e.expected_end::date)                     AS drift_days,
       e.freeze_days_used                                            AS charged_days,
       e.freeze_days_allowance                                       AS allowance,
       (e.end_date::date - e.expected_end::date) - e.freeze_days_used AS unexplained_days
FROM expected e
JOIN users u ON u.id = e.user_id
WHERE (e.end_date::date - e.expected_end::date) - e.freeze_days_used > 1
ORDER BY unexplained_days DESC;


-- ── 6. VERIFY (read-only) ───────────────────────────────────────────────────

-- 6a. No live membership should be over budget. Expect 0.
SELECT COUNT(*) AS over_budget_rows
FROM memberships
WHERE end_date >= now() AT TIME ZONE 'UTC'
  AND (
        (freeze_days_allowance IS NOT NULL AND freeze_days_used > freeze_days_allowance)
     OR (freezes_allowance     IS NOT NULL AND freezes_used     > freezes_allowance)
  );

-- 6b. Live memberships still without an allowance — these cannot freeze.
--     Should match what 0c reported; anything beyond that is unexpected.
SELECT COUNT(*) AS live_without_allowance
FROM memberships
WHERE end_date >= now() AT TIME ZONE 'UTC'
  AND freeze_days_allowance IS NULL;

-- 6c. Distribution sanity check.
SELECT plan,
       COUNT(*)                   AS live,
       MIN(freeze_days_allowance) AS day_allowance,
       MIN(freezes_allowance)     AS freeze_allowance,
       MAX(freeze_days_used)      AS max_days_used,
       MAX(freezes_used)          AS max_freezes_used
FROM memberships
WHERE end_date >= now() AT TIME ZONE 'UTC'
GROUP BY plan
ORDER BY plan;

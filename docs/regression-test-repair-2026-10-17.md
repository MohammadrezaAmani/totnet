# Regression repair — October 17, 2026

This release is based on the **full** `totnet-vpn-bot-2026-10-17.zip` source tree, not on an unknown partially-applied checkout.

## Changes

- Purchase/registration dispatcher no longer assumes the optional Hiddify administration handler exists in focused tests; production still invokes it when present.
- Plan catalogue caches the brand's free-plan policy together with plan rows and invalidates it on brand changes; a warm read no longer executes `SELECT ... FROM brands`.
- The catalogue respects admin `display_order`, and upstream deactivation is not bypassed by `sync_bot_settings=False`. Plans missing from an upstream non-archived response are both inactive and invisible.
- A normal referral purchase receives the configured **normal** referral rate when it does not actually earn an in-progress challenge reward; a Royal unlimited subscription still uses the 4% exception via `effective_reward_percent`.
- The emergency XLSX tests use DB-backed test sessions plus a local-memory cache, removing their accidental dependency on a production Redis process.
- Temporary 2/3-month enforcement tests are independent of `.env` overrides; user-facing duration numerals are Persian.
- Added regressions for hot cache reads, brand free-plan policy updates, and admin-hidden imported plans.

## Important local checkout mismatch

The provided test log reports `NameError: temporary_duration_blocked` in `apps/bot/handlers/purchase.py`. **The supplied baseline ZIP already imports that function**. Therefore the live checkout is not identical to this baseline (for example, it may contain a partial patch). Verify that the installed file contains:

```python
from apps.subscriptions.policy import TEMPORARILY_UNAVAILABLE_TEXT, temporary_duration_blocked
```

Use the full ZIP if the local tree does not match the original; do not apply a diff blind to a drifted checkout. No production bank/card credentials are included.

## Safe verification procedure

1. Back up the database and save the previous deployment/working tree.
2. In a **fresh checkout** of the 2026-10-17 release, use this release's replacement files or test `git apply --check totnet-regression-fixes-2026-10-17.patch` before applying its patch. Resolve merge conflicts manually if that check fails; **never** `--reject`/force-apply payment code.
3. Match the project's Python 3.14 environment and dependencies: `uv sync --frozen`.
4. Check for uncommitted schema changes: `uv run python manage.py makemigrations --check --dry-run`. **This patch changes no model fields and requires no new migration.**
5. Run `uv run python manage.py check`.
6. Run the full regression command:

```bash
uv run python manage.py test \
  apps.bot.test_plan_picker \
  apps.accounts.test_emergency_export \
  apps.referrals.tests \
  apps.orders.tests \
  apps.bot.tests
```

7. Test the actual bot with representative archived plans, locally hidden plans, admin display order, 2/3-month direct-payment callbacks, Royal unlimited 4% reward, and a complete card receipt flow **in staging**.
8. Only deploy after every test passes and human-review of wallet balances, reward transactions, and imported plans.

## Validation performed in the preparation environment

`compileall` passed for all Python sources, and patch whitespace/structure checks passed. We **could not run Django/aiogram integration tests**: the environment has Python 3.13 and lacks the full locked application dependencies; dependency installation did not complete. No claim is made that the provided 54 tests pass until run on the target Python 3.14 environment.

## Configuration

Leave `TEMP_DISABLE_2_3_MONTH_PLANS=True` on production until the catalogue is ready; tests for the block explicitly set this value to isolate them from machine-level `.env`.

If the live branch is different from the baseline, obtain a fresh ZIP of that branch before merging subsequent repairs to avoid losing changes made by other developers.

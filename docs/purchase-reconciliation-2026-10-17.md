# Repairing a partially-applied purchase-flow patch

The October 17 console log shows **56 tests; 10 failures and 3 errors**. The
unbound `temporary_duration_blocked` symbol is a runtime regression. Several
purchase-selector assertions still target the old `duration/traffic` selector
whereas the agreed purchase UI now starts at `bundle` (duration and number of
users together). The local checkout also contains `.orig` / `.rej` artifacts
from a previous `patch -p1` run. This indicates a conflicted working tree.

**Do not blindly overwrite the rest of the repository or its database.** The
reconciliation kit is a two-step, explicit operation:

1. `git apply` only **adds** the tool and versioned reference files. It does not
   change any executable bot module until you run the tool with `--apply`.
2. `--apply` backs up every modified file under `.purchase-repair-backups/` then
   updates:
   - `apps/bot/handlers/plan_picker.py` to the reviewed current-contract UI;
   - `apps/bot/handlers/purchase.py` **only** to ensure required policy imports;
   - `apps/bot/tests.py` **only** if it contains the known obsolete
     `select_plan_...` first-button assertion;
   - `apps/bot/test_plan_picker.py` **only if** you explicitly specify
     `--sync-regression-tests`: it replaces old picker tests with the regression
     tests for the new contract. The old tests are backed up, not discarded.

## Apply from the project root

```
cd ~/dev/.personal/totnet
# Download the repair patch first and use its actual local path.
git apply --check ~/Downloads/totnet-purchase-reconcile-2026-10-17.patch
git apply ~/Downloads/totnet-purchase-reconcile-2026-10-17.patch

# Inspect changes before modifying running code.
python tools/reconcile_purchase_20261017.py --check --sync-regression-tests

# Stop the bot worker / web workers during the change. Back up database first.
python tools/reconcile_purchase_20261017.py --apply --sync-regression-tests

git diff --check
python -m compileall -q apps/bot
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test \
  apps.bot.test_plan_picker \
  apps.accounts.test_emergency_export \
  apps.referrals.tests \
  apps.orders.tests \
  apps.bot.tests
```

`--sync-regression-tests` is **not** a substitute for fixing production code or
passing an E2E check. It is appropriate only because the previous test expected
the obsolete first-button UI. Review the diff before confirming.

## After repair

* Verify the new UI: select category -> select duration and users -> range ->
  plan -> payment, then use an old 2/3-month callback and ensure it cannot pay.
* Verify archived and locally hidden plans, admin sort priority, and gift days.
* Verify Royal unlimited 4% reward and wallet idempotency.
* Review `.orig` and `.rej` files **manually**. They are not removed by the kit.
* Production deployment is unsafe until all tests pass, migrations are checked,
  and real callbacks and payment flows are tested on staging.

## Rollback

The tool prints the backup directory. To restore a particular file:

```
cp -a .purchase-repair-backups/<printed-timestamp>/apps/bot/handlers/plan_picker.py \
  apps/bot/handlers/plan_picker.py
```

Repeat for other backed-up files, then rerun tests. Never use `git reset --hard`
on a working tree containing uncommitted changes you want to keep.

## Validation scope

The preparation environment only supports Python 3.13 without locked Django and
aiogram dependencies. The repair utility passed syntax and a simulated
import/assertion repair test. No successful Python 3.14 Django test run is
claimed for this release.

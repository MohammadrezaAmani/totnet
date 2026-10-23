# Fixes: subscriber details and Connectix account credentials (2026-10-23)

Basis: `totnet-rollout-rebased-actual-files-2026-10-22.zip` (the October 22 rebased release). The patch must be checked against the destination checkout; it may need rebasing when that checkout has drifted.

## Changed

- In My Subscriptions, clicking a subscription opens its **details** instead of opening all replacement/renewal plans.
- Details show only custom name (or a name-setting prompt), service, plan specification, remaining traffic and remaining time.
- Usage page shows **total consumed traffic** only. If Connectix returns an unlabelled nonzero number, the bot does not invent the unit; it explicitly says that consumption data is unavailable.
- `refresh_stats_<id>` is routed to the usage page (previously it had no handler).
- Account delivery and activation notification show both Connectix username and password (when available). No password is shown on general subscription list or details page.
- Added a plaintext `subscriptions.connectix_password` field; it is intentionally **not encrypted or hashed** per the owner's explicit instructions. Passwords must not appear in logs, admin list view or test fixtures from production. Database backups must be access-controlled.
- The initially generated password is saved after Connectix create; a nonempty password returned by Connectix replaces it. Existing subscriptions get passwords from the existing read-only Connectix client-list sync. Empty responses never wipe the saved value.
- When creating an account, an already-configured `display_name` is sent as the remote `name` field.
- The 3 Academy learning/download buttons are marked as success-green in both the Academy menu and the post-receipt menu.
- The misleading additional Telegram reply about failure to notify the admin is removed; a server-side error is logged and receipt remains available in the Django Admin queue. Operators MUST monitor pending receipts; notification delivery is not guaranteed.
- Removed duplicated renewal navigation rows.

## Known limitation — changing the remote account name

The captured Connectix API contract documents **create** and **list**, but **not an authenticated update/edit endpoint**. Changing `display_name` of an account that already exists is therefore stored in the bot only and **is not claimed to be changed remotely**. It must not issue guessed write requests to the seller API. Obtain an actual captured update request/response from the Connectix panel to implement safe synchronization of existing names.

## Apply

```bash
cd /home/debian/dev/.personal/totnet
PATCH="$HOME/Downloads/totnet-subscription-account-details-2026-10-23.patch"
test -f "$PATCH" && git apply --check --whitespace=error "$PATCH" && git apply --whitespace=error "$PATCH" && git diff --check
```

## Deployment checks (staging first)

```bash
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py migrate --plan
uv run python manage.py migrate
uv run python manage.py test apps.subscriptions.tests apps.vpn_providers.tests apps.bot.test_rollout_20261022 apps.bot.tests
```

Docker deployment after a database backup:

```bash
docker compose build web bot worker beat
docker compose run --rm web python manage.py check
docker compose run --rm web python manage.py migrate --plan
# Run migration once after checking the plan, with worker/bot/beat stopped.
docker compose stop bot worker beat
docker compose run --rm web python manage.py migrate --noinput
docker compose up -d web bot worker beat
```

For older subscriptions, after migration a normal `sync_vpn_users` cycle will fill the password only if the Connectix list endpoint actually returns the password. Test carefully with staging accounts.

## Verification status

Syntax compilation and patch application to the exact stated baseline were checked. Django integration tests could not be run in this environment (Python 3.13, no Django dependencies; project requires Python 3.14). No remote Connectix API write was made; no production migration was run.

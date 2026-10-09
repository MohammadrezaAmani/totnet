# Local bot runtime

This workspace runs TotNet's Python bot, worker, and Beat scheduler as systemd user services. PostgreSQL runs in `totnet-db-1`; the isolated Redis instance is `totnet-local-redis` on `127.0.0.1:6385`. Native Python processes can use the host's loopback SOCKS proxy on port 2080. Credentials and connection URLs remain in the untracked `.env`.

```sh
systemctl --user status totnet-bot totnet-worker totnet-beat
journalctl --user -u totnet-bot -f
systemctl --user restart totnet-bot totnet-worker totnet-beat
```

Service definitions are under `~/.config/systemd/user/totnet-*.service`. Beat state is under the ignored `var/` directory. The PostgreSQL and Redis data live in Docker volumes. The bot's configured username is discovered with Telegram `getMe`, not hardcoded.

For an immediate catalog refresh:

```sh
.venv/bin/python manage.py setup_bot --sync-plans
```

For verification:

```sh
uv sync --locked
.venv/bin/python manage.py check
.venv/bin/python manage.py test --noinput
```

Automated purchase tests use synthetic Connectix responses. Live checks authenticate and read catalogs only; no test customer or charged account is created. Paid card transfers need a real `PaymentCard` and enabled `BrandPaymentMethod` with type `card_transfer` in Django admin. The bot provides a support response when payment details are absent.

The purchase page opens the service menu, then duration buttons and four volume ranges. Selecting a range opens the user-count page, followed by paginated matching plans. Back preserves selections; changing duration or volume clears later choices. Options come only from the selected service's cached sellable catalog. Unsupported combinations are never synthesized. Old picker sessions are rejected after returning to the service menu or entering checkout.

The `subscription-plan-catalog:v2` cache contains scalar plan data and completed purchase counts scoped to the brand (reward redemptions excluded). Admins can mark subscription plans `is_featured` for «تجویز ویژه دکتر»; normal cache invalidation applies. Connectix supplies `display_order` during its sync. Free trial plans remain selectable through the filters. The Connectix catalog refreshes every 15 minutes and warms the cache; browsing does not call Connectix. Checkout rereads the actual plan and price.

Keyboard styles use Telegram Bot API `primary` (blue), `success` (green), and `danger` (red). No Premium-only custom emoji is used. Source: https://core.telegram.org/bots/api#inlinekeyboardbutton

For a full Docker application deployment, stop the native bot, worker, and Beat services first to avoid duplicate pollers and schedulers. Use a container-accessible database URL instead of the native loopback URL in `.env`. The proxy must also accept connections from Docker's host gateway; rewriting localhost to `host.docker.internal` cannot reach a proxy bound only to host loopback.

## Bot revision: 2026-10-09

Purchases now use duration buttons above four volume ranges, then a separate user-count page and the actual matching plans. Royal uses 10–30, 30–50, 60–100 GB and unlimited; Normal starts at 20 GB. The normal service buttons are uncolored; only «تجویز ویژه دکتر» has a different color. Existing device selections are preserved when migrating to multiple devices, and returning users see the main menu without replaying their configured welcome message.

Academy defaults come from [Connectix](https://connectix.space/fa/), [Happ](https://www.happ.su/main) and [v2rayNG releases](https://github.com/2dust/v2rayNG/releases). Each app/platform has a link screen. Only the explicit installer button sends a file; iPhone and macOS screens use links only. Defaults and uploaded files remain editable in Django Admin under Useful content. Existing entries are preserved. New bot setup seeds the same defaults and ticket categories when none exist.

Most Connectix Android and Happ installers exceed the Telegram cloud Bot API upload limit. Set their existing Telegram `file_id` values in `.env` and restart `totnet-bot`. These IDs must belong to the bot specified by `TELEGRAM_BOT_TOKEN`; other brands can configure IDs in Django Admin. The variables are:

```dotenv
CONNECTIX_ANDROID_PRIORITY1_FILE_ID=
CONNECTIX_ANDROID_PRIORITY2_FILE_ID=
CONNECTIX_ANDROID_PRIORITY3_FILE_ID=
CONNECTIX_WINDOWS_FILE_ID=
CONNECTIX_LINUX_FILE_ID=
HAPP_ANDROID_FILE_ID=
HAPP_WINDOWS_FILE_ID=
HAPP_LINUX_FILE_ID=
V2RAYNG_ANDROID_ARM64_FILE_ID=
V2RAYNG_ANDROID_ARM32_FILE_ID=
CONNECTIX_LINUX_DOWNLOAD_URL=
```

Android priority 1 uses ARM64, priority 2 uses ARM32, and priority 3 uses the universal package. Files below the upload limit are saved locally and their Telegram file IDs are cached after delivery. The publisher's Linux DEB link returned 404 during this revision; its screen uses the official download page until `CONNECTIX_LINUX_DOWNLOAD_URL` and/or `CONNECTIX_LINUX_FILE_ID` is supplied. File IDs in `.env` override cached IDs. An administrator can send a document to the bot to receive its `file_id` for configuration.

Sixteen days after the first fully paid purchase, the existing gamification schedule sends one profile-completion reminder. Its form collects birth date (Persian or Gregorian), phone, then work/living location, in that order. Completing it grants one pill once, tracked by a durable profile marker. This bonus carries no purchase commission amount and counts toward the next group of three pills; the cash values of purchases in that group are preserved.

Ticket creation supports optional device selection and durable ticket IDs. New installs receive four support categories when no categories exist. Ticket/list/detail rendering escapes customer text. Notification delivery failures do not roll back a committed ticket.

Gift purchases accept the intended recipient's Telegram username even before they register. The purchaser receives the account credentials and can pass them to the recipient. To attach the gift, the recipient opens «ثبت اشتراک فعال» and enters the VPN username; their Telegram identity must match the designated account. Existing non-gift claims continue through explicit ownership review. The gift earns a single purchaser reward after that claim and full payment, using the campaign percentage saved at payment. Ordinary referral purchases, including repeats, use the active campaign percentage. Campaign target eligibility and settlement rules remain separately tracked.

No test suite was run for this revision, at the user's request. Python syntax, Ruff, Django system checks and migration consistency were checked. Migrations were applied to the configured PostgreSQL database after a local backup under `var/backups/`.

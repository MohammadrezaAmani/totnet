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

The `subscription-plan-catalog:v3` cache contains scalar plan data and completed purchase counts scoped to the brand (reward redemptions excluded). It resolves Connectix's default group to Royal before service filtering, including plans imported before category mapping existed. Empty service snapshots are reread from the database once per lookup. Admins can mark subscription plans `is_featured` for «تجویز ویژه دکتر»; normal cache invalidation applies. Provider ordering is used on import and when `sync_bot_settings` is enabled; otherwise admin ordering is preserved. Free trial plans require the brand's `show_free_plans` flag. The Connectix catalog refreshes every 15 minutes and warms the cache; browsing does not call Connectix. Checkout rereads the actual plan and price.

Keyboard styles use Telegram Bot API `primary` (blue), `success` (green), and `danger` (red). No Premium-only custom emoji is used. Source: https://core.telegram.org/bots/api#inlinekeyboardbutton

For a full Docker application deployment, stop the native bot, worker, and Beat services first to avoid duplicate pollers and schedulers. Use a container-accessible database URL instead of the native loopback URL in `.env`. The proxy must also accept connections from Docker's host gateway; rewriting localhost to `host.docker.internal` cannot reach a proxy bound only to host loopback.

## Bot revision: 2026-10-09

Brand Admin → Contact & Bot includes «نمایش پلن‌های رایگان در ربات» (`show_free_plans`), disabled by default for existing and new brands. The bot reads the flag live and hides plans whose current discounted price is zero or less from purchase, featured, trial and renewal options while disabled. Old free-plan selection buttons are also rejected. Enabling it exposes eligible free plans on the next menu visit without a service restart; existing subscriptions remain visible.

Connectix's `default` group (including an empty group label) is classified as Royal. Imported plan names follow the actual device count, traffic and duration, for example `2×20g_1m_royal`; the original provider title stays in `upstream_plan_name` for provisioning. Duplicate specifications receive an identifier suffix to preserve the brand/name constraint. Purchase and renewal options show compact Persian specifications, such as `💉۵۰گیگ 👤۴کاربر 📅۳ماه`, with a separate price icon. Catalog sync preserves the provider mapping; local display names remain editable.

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

## Bot revision: 2026-10-10

Volume buttons are limited to actual matching plans for the selected duration. The 30 GB boundary is included in both ranges that mention it. Stale or unavailable volume choices refresh the current menu rather than leaving an error dialog. Royal remains mapped to the provider's default group, Economic/Sublink to Normal, and Iran Access/Static IP to Iran IP. Migration 0014 makes mapped imported plans locally visible; the provider's sellable-plan metadata and active status still gate availability. Free plans remain hidden unless the brand enables them.

Subscription Plan Admin controls name, visibility, ordering, featured status and discounts. `sync_bot_settings` defaults to false so name/visibility/order edits survive catalog sync. `sync_price` defaults to true; editing a price in admin automatically opts it out of price sync unless that flag is explicitly changed. Provider cost, currency, IDs, traffic, duration and device metadata continue to sync and are read-only for imported Connectix plans. Browsing respects admin order.

Purchase and renewal amounts show the price after usable cash wallet credit, excluding frozen funds and amounts beyond spending limits. Browsing does not debit money. Creating/continuing checkout applies cash through confirmed wallet payments with row locks and ledger idempotency; card transfer receives only the remainder. Discount calculations round to two decimals with HALF_UP; usable credit rounds down to the same precision. Cancelling an unfinished wallet-part-paid order refunds the original payments once. The purchase menu offers resumption of an unfinished cash-part-paid order. Fully paid orders cannot be cancelled through this path, and partial non-wallet payments require support review.

The separate gift purchase option has been removed. «خرید برای دیگری» collects a Telegram username in the existing message and replaces that prompt with payment/activation status; old gift buttons return to plan details. The internal gift order type remains the recipient-ownership and purchaser-reward record for this flow. Existing claim verification and campaign snapshots remain in force.

Gamification notifications persist their text/buttons in the existing ledger and use a retryable Celery delivery task. `sent_at` is written only after Telegram accepts the message, not after publishing to Redis. The 15-minute scheduler recovers unsent deliveries even after the original reminder window. Blocked bots are recorded as abandoned deliveries. Notification timing and purchase campaign snapshots use payment verification time, with creation time as the fallback for legacy confirmed payments.

Challenge Program Admin includes its tier editor. Current configured Propzino settings are a 5-day offer delay, 7-day duration and 13% rate, with targets 1/3/5/8 and entry amounts 50,000/150,000/250,000/350,000 T. Reminders occur at 5/3/1 days remaining. Completed challenge outcomes and wallet frozen/cash balances are read-only in admin; program and future-tier settings stay editable. Order admin and provisioning require the full confirmed payment total for paid orders.

Brand Admin includes payment-method and bank-card editors. Bot setup supplies a default card-transfer method when none is configured; a real active bank card still must be configured by the brand administrator. No placeholder card details are created. This revision was checked with source/static/Django/migration checks; no test suite or manual Telegram session was run.

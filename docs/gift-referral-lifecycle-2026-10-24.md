# Gift handoff, referrer notification, onboarding and lifecycle campaign rollout

Base: `totnet-referral-deeplink-attribution-fix-2026-10-23.zip` **plus** `totnet-fix-referral-for-update-2026-10-24.patch`. This is an incremental change; do not apply it to other revisions without `git apply --check`.

## Referrals

- A new real referral creates the usual `Referral` row and emits `referral-joined:<brand_id>:<referee_id>` to the **inviter**, not to the new user. The notification is queued after the attribution transaction commits; a 5-minute Celery Beat recovery reconciles missed join notices from the prior seven days. Dedupe is in `GamificationNotification` and does not award cash or pills.
- Invalid or ownerless codes continue to show a failure, so silent errors do not corrupt attribution. Existing users cannot replace an established referrer.
- The gift claim link also attributes a *newly registered* recipient to the purchaser; an existing recipient gets the gift but no new referral. No duplicate referrer cash award is created by join notification.

## Gift checkout

- On receipt submission a gift purchaser sees `در انتظار تأیید پرداخت`, not a normal subscriber's activation or academy menu. The claim link is **not issued on mere receipt submission**.
- After a fully confirmed gift payment, the purchaser gets an idempotent notification containing the single-use `?start=gift_<token>` link, and a second message explaining the **same** gift-specific link as the invitation/claim channel. An independent second link would allow confusion between the sole claim token and referral attribution; the same token is safer and lets the recipient claim once only.
- The actual expiry is `Brand.gift_expiry_hours` (default **72** hours), written as a Jalali expiry timestamp. Gift provisioning runs only after token claim. Recipient is prompted to name the subscription (example: **آیفون خودم**) in `اشتراک‌های من`.
- Name change for a *previously provisioned* account is local to the bot until a verified upstream Connectix update-name API exists.

## Required onboarding and channel membership

- For an initial registration the mandatory steps are **name, phone, primary device**. Optional profile fields remain available for the profile-completion cash reward.
- An opted-in channel-membership requirement (`BrandConfiguration.custom_fields.required_channel_id` and `required_channel_invite_link`) is enforced **after** the primary device step. The joining bot must be an administrator able to check channel membership. If enabled, `auto_approve_join_requests` can accept pending requests; this requires corresponding bot privileges.
- Older members are not retroactively forced through onboarding solely because of this feature.

## Dynamic rules already supported + added triggers

Django Admin > Referrals > Notification rules. These trigger delays use **minutes** and are checked by Celery Beat every minute:

| When | Trigger | Delay |
| --- | --- | ---: |
| User reaches 11 days since first active subscription started | `first_active_subscription` | `15840` |
| User reaches three weeks after their first **fully paid** purchase | `first_purchase` | `30240` |
| User first joined via a specific referral link, independently of purchases/profile completion | `referral_link_join` | `5` |

For a link-specific message select `referral_link` (otherwise the `referral_link_join` rule applies to *any* new referral-link registration in the brand). Give the rule `audience=all`, `kind=custom`, and enable it.

Use the following *example* text in the link's five-minute rule, editable per brand:

> 🎁 لینک معرفی شما یک هدیه برای اولین خرید دارد. کد: {coupon_code}. مهلت: {coupon_expiry}. فرصت استفاده از خدمات را از دست ندهید!

`{coupon_code}` and `{coupon_expiry}` are populated from the first registration's matching promotional link, HTML-escaped. They are empty if the link has no configured coupon. Do not claim that VPN time stays unconsumed until first connection without independent confirmation from the upstream provider: the current subscription code starts time when activated/provisioned.

## Auto coupon for first purchase from a referral link

1. Create a fixed-value or percentage `Coupon` for the **same brand** with `new_users_only=True`, a valid absolute date window, and `max_uses_per_user=1`.
2. In Django Admin > Referral links, select the coupon under `welcome_coupon`. Set `welcome_coupon_valid_hours=48` (or the desired hours). An expired/disabled link cannot be used to redeem its attached coupon.
3. A newly registered visitor arriving through that link gets an immutable `ReferralClick(is_registration=True)`. The checkout automatically applies its matching coupon before quoting a bank-transfer amount, or falls back to normal price if the coupon is invalid, exhausted, expired or already used.
4. Only that visitor may use the code, only within the configured time after the **initial link entry**, and only for the first fully eligible order. Merely guessing/forwarding the coupon code is insufficient. Usage, per-user limits and max total uses are serialized with row locks.
5. Create an active link-specific `NotificationRule` as above to send the five-minute welcome copy. Creating a coupon/link alone does **not** automatically create a notification rule.

## Tests before deployment

```bash
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test \
  apps.referrals.test_campaign_rollout_20261024 \
  apps.bot.test_referral_deeplink_fix \
  apps.referrals.test_purchase_notifications \
  apps.orders.test_checkout_links \
  apps.bot.test_registration \
  apps.referrals.tests
```

Run migrations **only after a database backup**, verify staging first, and rebuild/recreate bot + web + worker + beat (Celery Beat needs the new schedule). Verify gift claim + redeem and notification `sent_at`, not only notification creation. This environment performed syntax/patch-smoke validation only: **Django and actual Telegram/Connectix tests were not run**.

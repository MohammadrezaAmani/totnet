# Totnet — 2026-10-22 staged rollout changes

This patch is generated against the previously delivered **totnet-gift-direct-card-checkout-2026-10-18.zip** baseline. It is **not** a merge against your uncommitted live server tree. Do not force-apply or overwrite modified production files.

## Implemented in code (requires staging tests)

- Initial onboarding requests name and Iranian mobile number only, with an explicit explanation about outage and subscription-status notifications; devices are no longer required. Obsolete callback for skipping phone also cannot bring back the required-device page.
- Best-effort Telegram deletion of private typed inputs and tracked obsolete onboarding/profile prompts; Telegram still controls deletion permissions and age limits. Contact-sharing keyboard is removed.
- Optional new-user-only private channel gate, server-side membership verification and join request approval (bot must be an admin with suitable privileges). BrandConfiguration.custom_fields controls `required_channel_id` (signed numeric channel ID), `required_channel_invite_link` (private request URL), `auto_approve_join_requests` (boolean). **Enable only after successful private-channel testing.**
- Profile view hides Telegram username, uses Jalali admission/birth dates, and shows only residence province rather than work/residence separately. Compact profile editor excludes email and allows optional gender and multiple devices. An eligible voluntarily completed profile receives **one** idempotent 10,000-toman wallet bonus, not a pill notification. Only brands with currency `T` or `IRT` qualify.
- Wallet overview uses new terminology: current cash balance and sum of direct deposits. Removes membership date and total purchase count. Integer toman money presentation and many other dates changed to Jalali.
- Purchase plan ranges: Royal 10–50, 51–99, 100+ GB; Normal 30–60, 61–99, 100+ GB. Intervals are nonoverlapping, and existing 2/3-month minimum traffic checks remain. The purchase picker links to the service guide and the plan list makes wallet-credit-adjusted prices explicit.
- Admin-managed order-scoped fixed or percentage discount coupons before card amount is reserved. User sees amount discounted, without needing to know coupon type. Usage limit, ownership, new-user, plan, currency rounding, and quote checks are transactional. Does not silently permit 100% free checkout on an existing paid order.
- Existing subscriptions can be named individually for local bot display; owner checks are required. A VPN username alone still cannot reassign someone else's subscription.
- Configurable reward percentage by brand, service and plan type from `ServiceRewardRate` in Django Admin. Royal unlimited remains a hard 4% override. Toman-derived cash rewards use integer quantization.
- One-time first-visit section explanations controlled by BotState.seen_sections; admins can override copy via `BrandConfiguration.custom_fields.first_visit_messages` map.
- Renewal keyboard limited to 8 plans/page with navigation and ownership checks, to prevent Telegram `reply markup is too long` errors.
- New regression tests for promo amounts, permission boundaries, wallet profile reward, royal rate, Jalali and large-catalog renewal; onboarding legacy regression updated to new no-device contract.

## Not completed: must NOT be represented as shipped

1. Accurate historical `total cash rewards **spent**`: the existing wallet pools direct topups and rewards. Previous spending transactions do not reliably attribute withdrawn units to their source. Showing `all wallet spend` as discounts is **financially wrong**. The wallet currently explains why the exact metric is unavailable. A source-aware sub-ledger and non-destructive backfill/reconciliation are required.
2. Doctor's **two-plan bundle** as one atomic checkout (one Normal unlimited + one Royal 10GB) needs an order-item model and partial-failure provisioning logic. Current featured plans are independent, so there is no bundle guarantee.
3. Automatic verification of independently-owned provider accounts: only accounts already linked to the same Telegram user (or designated gifts) can be automatically associated. Unknown usernames create a pending claim; approving them based on username alone would enable account takeover. Provider-side ownership proof/invitation tokens required for safe automation.
4. Automatic bank payment confirmation: a distinctive amount is a matching hint, not settlement evidence. Do not enable automatic provisioning based solely on an SMS.
5. Full project migration, Django tests, device/channel joins and financial stress tests have not been executed in this environment (requires Python 3.14 and installed project services/dependencies). No production approval.
6. Previously granted single pill reward is not silently converted into 10,000 cash for historical users; reconciliation would require a separately approved, idempotent data migration.

## Safe deployment procedure

1. Ensure `.git` changes and deployment branch are backed up, including a logical Postgres backup **before** migration.
2. Apply the patch **on the exact baseline or a staging branch**. On a changed server tree, `git apply --check` may fail; stop and rebase rather than `--reject`, `--3way`, or copying individual files.
3. Use staging to run `python manage.py check`, `python manage.py makemigrations --check --dry-run`, `python manage.py migrate --plan` then `python manage.py migrate`, and the tests shown below.
4. Run real Telegram onboarding, text/contact deletion, multi-account claim, new-user channel, promo, wallet ledger and provider provisioning tests, and verify no duplicate awards under simultaneous callbacks.
5. Observe bot/worker logs in staging and reconcile a full test payment and gift before releasing real users.

```bash
python manage.py test \
 apps.bot.test_rollout_20261022 \
 apps.bot.test_registration \
 apps.bot.test_plan_picker \
 apps.bot.tests \
 apps.orders.test_checkout_links \
 apps.orders.tests \
 apps.referrals.tests \
 apps.accounts.test_emergency_export
```

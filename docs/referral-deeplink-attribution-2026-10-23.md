# Referral Deep Link — audit and deployment

Only the first `?start=<code>` performed on **new** Telegram account signup is attributed. Existing accounts cannot be re-assigned with a later referral link. A retry of an explicitly recorded first-registration click can repair a preexisting failed link.

The referral relation is recorded immediately, before name, phone or other profile details are available. Referral reward eligibility is assessed separately on **fully confirmed paid** purchases. Daily/monthly caps and verified-phone requirements apply to rewards, **not to recording signups**.

The referral menu now separately shows **joined via your link**, **confirmed purchasers**, and **active subscriptions**. The count of purchased referrals remains zero until a qualifying paid purchase actually occurs.

## Link diagnosis (database-backed)

```bash
cd ~/totnet
docker compose exec -T web python manage.py audit_referral_deeplink --code L436ZP00
```

Check: code exists; `Brand` and `Configured bot` correspond to `DRVPN_Private_bot`; `Active=True`; `Inviter user PK` populated. A link with no owner is a campaign-only link and **cannot** assign a personal inviter. Check `Recorded link visits` and `Unattributed, evidenced FIRST registrations`.

If only first-registration clicks are missing referral records, preview then explicitly repair:

```bash
docker compose exec -T web python manage.py audit_referral_deeplink --code L436ZP00 --apply
```

This is idempotent, does not rewrite existing inviters, does not grant points, does not issue any payments, and does not recover people who visited the link only *after* their first account registration. For those, manual fraud-aware examination of onboarding events is required.

## Tests

```bash
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test apps.bot.test_referral_deeplink_fix apps.bot.test_registration apps.referrals.tests apps.referrals.test_purchase_notifications
```

Use staging first. `git apply --check` must pass before `git apply`; no schema migration is required.

## Important limits

- The link's **actual** record cannot be verified without access to production database.
- `User.is_verified` is separate from having supplied a phone number; if the reward program requires verification, rewards may still be deferred until verification has occurred.
- Notification delivery depends on the separate payment/referrer notification job and Telegram availability; a count of registered joiners alone is not proof of payment or successful notification.

# Audit and fixes journal

## 2026-09-26

### Checks failed before changes

- Django settings tried to parse the configured `DEBUG=release` deployment label as a boolean and failed during settings import.
- Celery Beat referenced notification, broadcast, subscription-statistics, and analytics task paths with no task modules in this checkout.
- Provider tasks were declared with `async def`, which standard Celery workers do not await. They also used synchronous ORM calls from async task bodies.
- Provider sync wrote `VPNProvider.last_sync`, a field that did not exist.
- Provider health code changed an active provider to ERROR after one unsuccessful request and treated one health failure as an activation decision.
- Provider discovery depended on a Hiddify/PasarGuard module being imported elsewhere first.
- Hiddify/PasarGuard HTTP clients disabled TLS certificate verification.
- Base provider logging could include query strings, request/response bodies, and raw exceptions.
- Static checks found unused variables/imports, a duplicate admin method definition, and a confusing comprehension variable.
- The repository had no tests discoverable by Django. The local SQLite database contains no subscriptions; checks and migrations were not run against or applied to that database.

### Changes made

- Parse common development/production DEBUG labels and avoid configuring a missing static directory.
- Honor the SQLite file path in `DATABASE_URL`; migration validation ran against a temporary SQLite database rather than the existing release-configured database.
- Add `last_sync` to VPNProvider with a new migration.
- Make provider factory import the supported Hiddify/PasarGuard adapters reliably and fail explicitly for unimplemented provider types.
- Convert provider Celery entry points to synchronous task functions that bridge asynchronous API methods with `async_to_sync`.
- Skip unsupported providers in fan-out sync/health tasks, close client sessions, and avoid deactivating providers because of a health-check failure.
- Remove nonexistent task references from Beat and schedule the implemented provider cleanup jobs instead.
- Enable TLS certificate verification for Hiddify/PasarGuard HTTP clients.
- Sanitize base provider logs and base health-check errors.
- Mask provider API keys in Django Admin and preserve stored values when the masked inputs are left empty; remove the legacy Connectix password from subscription admin forms.
- Fix the reported Ruff issues, including duplicate admin search method, stale variables, and imports.
- Add contract tests for implemented provider construction, synchronous Celery Beat tasks, Connectix auth/list/create behavior, response parsing, and safe GET reauthentication. The tests use mock HTTP transport only.
- Add Connectix environment settings without including credentials.

### Connectix and bot flow

- Implement the captured seller API contract in `connectix.md` and document the supported surface in [connectix-api.md](connectix-api.md).
- Register a Connectix adapter with login, seller health, paginated client lookup, and one-shot client creation. Do not retry create requests because API idempotency was not observed.
- Add Connectix plan/group/device mapping fields and require complete mappings before creating clients. Prefer a plan's mapped active provider during payment handoff.
- Persist provider remote identity before readback, activate local subscriptions only after readback confirms active state and a subscription link, and let retries reconcile by remote ID.
- Route Connectix subscriptions through their actual returned subscription link in the bot. Remove the stored Connectix password column and keep links/passwords out of admin and logs.
- Add migrations for provider sync time, Connectix mappings and remote identities, order/subscription uniqueness, and removal of the legacy password field. Validate migrations only on a temporary SQLite database; no migration was applied to the existing release-configured database.

### Not yet repaired

The captured Connectix contract has no verified renewal, suspend, deletion, traffic adjustment, or direct lookup operation, so those actions are not implemented. No live Connectix account was used for mutation testing.

## Additional implementation

- Wallet checkout is now row-locked and idempotent. Wallets are unique per user and brand, confirmed order payments are unique, and wallet ledger transactions have idempotency keys. Wallet and reward-value balances now retain fractional conversion precision.
- Referral attribution is immutable per referee and brand, rejects self-referrals, and updates referrer registration counters. Valid referral-link opens increment click counts.
- Confirmed profitable purchases can award Decimal-based level-one points once. The purchased order snapshots upstream cost so later plan edits do not change the recorded margin.
- Added configurable reward services, immutable point-value snapshots, lifetime point valuation, ordered fractional point boxes, an auditable ledger, and transactional reference-service changes. Completed boxes convert to wallet value; incomplete boxes retain value when rebased, including sub-point precision.
- Added replay-safe point redemption. It atomically spends liquid points and creates one zero-price order and pending subscription. Reward and normal paid orders now enter a Celery provisioning task; duplicate task delivery is gated by a short database lease.
- Reward, referral, and point-box bot views now read configured lifetime levels and point-box state rather than displaying invented fixed reward amounts. Admin tools configure reference services, point-box layouts, and lifetime valuation; reward records are read-only and scoped by brand.
- Profile, statistics, start-menu, and admin user summaries now read lifetime/liquid reward points from `RewardAccount`; legacy user reward fields are no longer used for display or updates.
- Added a periodic recovery task for confirmed referral rewards whose enqueue or processing failed.
- Moved provisioning orchestration from the Telegram purchase handler into `apps/subscriptions/services.py`. Paid orders enqueue provisioning after commit; task delivery uses a short database lease and successful activation notifies the purchaser and beneficiary.
- Routed the shared subscription screens through a provider-aware handler. Connectix returns its verified subscription URL, Hiddify keeps its actual config retrieval, and unknown providers no longer receive fabricated VLESS URLs. Purchasers and beneficiaries can view shared subscriptions.
- Added explicit provider capability declarations. Admin rejects activation of providers without a provisioning adapter and rejects active Connectix plans without the observed plan/group/device mapping.
- Provisioning now records a durable state, sanitized error code/message, and whether retry is safe. A create with an ambiguous outcome is paused for review; reconciliation is retryable when a remote identity was saved.
- Connected the scheduled provider sync to Connectix's observed paginated client list. It refreshes local active/suspended/expired status and saved subscription links for known remote IDs. Usage remains raw metadata because the panel's unit is not established.
- Added migrations for reward ledgers/redemptions, money precision, upstream-cost snapshots, and provisioning leases. Existing order snapshots are backfilled from the plan cost available when migration runs.

### Current verification

- `python manage.py check`: passes.
- `python manage.py test`: 26 tests pass, including mocked Connectix API and status-sync tests, referral, point-box, redemption, wallet idempotency, provisioning retry, gift ownership, and Connectix order-routing tests.
- `ruff check .`: passes.
- `makemigrations --check --dry-run`: no model changes missing migrations.
- `ruff format --check .` reports formatting differences in 44 existing/touched files; no repository-wide formatting was applied as part of this work.
- The test runner applied all migrations to an ephemeral test database. The existing local/release database was not migrated.

### Remaining work

- Connectix traffic values are retained raw; reliable numeric usage synchronization requires confirming the seller panel's traffic unit and date semantics. Connectix create calls cannot be blindly retried because panel idempotency was not verified.
- Subscription management actions, renewals, expiry notifications, subscription import/claim verification, and the full onboarding/profile flow need further repair. Gift checkout now resolves a registered same-brand recipient, and phone sharing uses Telegram's contact request.
- Legacy `User.reward_points` and `User.level` columns remain in the database for compatibility, but are no longer used by the bot or admin summaries. They can be removed after confirming no external integrations read them.
- The panel's seller-plan read request returned HTTP 500 during read-only verification. Plan mappings must be entered from seller-panel metadata until Connectix corrects that endpoint. No remote account was created or modified.

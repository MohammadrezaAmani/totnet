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

The broader payment/wallet lifecycle, referral point boxes, provider-neutral management actions, gift ownership across every subscription bot handler, and safe import/claim flows still need dedicated work. The captured Connectix contract has no verified renewal, suspend, deletion, traffic adjustment, or direct lookup operation, so those actions are not implemented. No live Connectix account was used for mutation testing.

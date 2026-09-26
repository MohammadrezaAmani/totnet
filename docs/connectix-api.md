# Connectix seller API integration

This integration uses only the seller-panel requests captured in `connectix.md`. The capture was sanitized before keeping it in the repository: it contains no seller credentials, bearer tokens, client passwords, customer identifiers, or subscription links.

## Configuration

Set `CONNECTIX_BASE_URL` (defaults to `https://api.connectix.vip`), `CONNECTIX_USERNAME`, `CONNECTIX_PASSWORD`, and optionally `CONNECTIX_TIMEOUT_SECONDS`. The Python setting is named `CONNECTIX_API_BASE_URL`. The panel URL is `https://seller.connectix.vip/`; API calls use `/v1/seller` and bearer authentication obtained from the login endpoint. Never put credentials or tokens in source control or logs.

## Implemented calls

- `POST /auth/login`: acquire a bearer token using the configured seller credentials.
- `GET /seller-data`: validate access and read seller metadata.
- `GET /clients?page=…&is_export=false&recordPerPage=30&quickFiltersValue=`: find a created client by its returned ID, paging through the observed list response.
- `POST /clients/store`: create a client using the observed form-shaped request. The request is sent once; the adapter does not retry it because idempotency is unverified.

Create flow saves the returned remote ID before trying the list readback. If readback fails, a later retry looks up that same remote client rather than creating a duplicate. The local subscription becomes active only after the readback confirms an active, non-expired client and supplies a subscription link. The link is a bearer secret and is hidden from admin list/detail fields; it is sent through the bot only to the purchaser or assigned subscription owner, both of whom can access that subscription.

The temporary Connectix client password is used only in the create request. It is not stored locally or sent to the customer. The username is retained for support. Username generation follows the observed sample shape, but seller-side validation rules were not present in the captured contract.

## Plan mapping

Each local plan must reference a Connectix provider belonging to the same brand, plus the mapped seller plan ID, group ID, group name, plan name, and device count. The seller plan and group IDs and device count are available from the captured metadata/list operations. Unmapped plans remain pending with a recorded safe error; no fabricated connection URL is generated. Configure the provider credentials through environment settings.

The supplied account authenticated successfully with read-only calls to seller-data and client metadata; metadata returned 45 plans and 4 groups. The live seller-plans read returned HTTP 500 with the documented query keys, so the purchase path does not depend on that endpoint. Use identifiers from client metadata and the sanitized captured plan details for local mappings until Connectix clarifies the failing read.

## Deliberately unsupported

The captured requests do not establish contracts for renewals, suspend/activate, deletion, traffic changes, plan changes, online status, or transaction/balance operations. Those methods are not implemented. Traffic units, date conversion, rate limits, username rules, and error payloads need verification from sanitized panel captures before extending the adapter.

## Safe verification

The automated contract tests use an HTTPX mock transport and synthetic seller/client data. A separate live check authenticated with read-only calls; no client was created. A real create call should only be exercised with an explicitly designated disposable seller account and test plan.

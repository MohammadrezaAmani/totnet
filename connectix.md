# Connectix seller API contract (sanitized)

This contract is transcribed from the supplied seller-panel request captures. All credentials, bearer tokens, customer names, client identifiers, passwords, traffic links, and subscription URLs have been removed.

## Service and authentication

- API host: `https://api.connectix.vip`
- Panel origin: `https://seller.connectix.vip`
- API prefix: `/v1/seller`
- Authentication: login returns a bearer token; subsequent panel calls send `Authorization: Bearer <token>`.
- The panel sends JSON and uses `Accept: application/json, text/plain, */*`, `Accept-Language: en`, `Origin: https://seller.connectix.vip`, and `Referer: https://seller.connectix.vip/`.
- Cookies/CSRF were not present in the captured requests.
- The login capture also contained an Authorization header. Its purpose is unclear; credentials were removed, and the implementation does not replay that captured token.

## Verified calls

### Login

`POST /v1/seller/auth/login`

Request keys: `email`, `password`, `rememberMe`, `device_browser`, `device_os`.

Response keys include `token`, `seller` and `seller_notifications`. Seller data includes an ID, name, short ID, sub-seller flag, role details, wallet balance, and locale. Secret values are intentionally omitted.

### List clients

`GET /v1/seller/clients?page=1&is_export=false&recordPerPage=30&quickFiltersValue=`

Response keys: `total_clients` and paginated `clients`. The pagination object contains `current_page`, `data`, `last_page`, `next_page_url`, and `total`. Client records contain `id`, `username`, `password`, `name`, `expire_date`, `remains_days`, `is_active`, `is_expired`, `used_traffic`, `plan_name`, `group_name`, `subscription_link`, `outline_link`, `used_devices`, and activity fields. Values are omitted because these records contain customer credentials and bearer links.

### Client form metadata

`GET /v1/seller/clients/meta-data`

Response keys: `seller_plans` (IDs and titles) and `groups` (IDs, names, translated names).

### Seller account

`GET /v1/seller/seller-data`

Response contains the same seller and notification structures returned at login.

### Dashboard

`GET /v1/seller/dashboard?freeAndPremiumChartType=…&type=…`

Response contains `transactionReports` with aggregate transaction, income, and profit summaries. Query value semantics were not established.

### Create client

`POST /v1/seller/clients/store`

Observed request keys include `name`, `password`, `username`, `plan_id`, `group_id`, `count_of_devices`, `enable_plan_after_first_login`, `group_name`, `plan_name`, `is_active`, `is_expired`, `subscription_link`, `outline_link`, `used_devices`, and optional contact/metadata fields. The panel sent a full form-shaped object; the minimum accepted set and server-side validation rules have not been established. The implementation must not send an incomplete or guessed request.

Response keys: `message`, `client_id`, `text_to_copy`.

### Seller plans

`GET /v1/seller/seller-plans?forClientPage=…&is_archived=…`

Response contains `seller_plan_group` with seller plans and `groups`. Plan records include `id`, `title`, `price`, `period`, `period_unit`, `traffic_amount`, `count_of_devices`, `type`, `is_displayed_in_panel`, `is_displayed_in_robot`, and group translations. The response also contains `allowed_plan_periods`.

## Capabilities not observed

No captured contract demonstrates renew/extend, suspend/activate, delete, reset/add traffic, change plan, direct client lookup, status update, usage refresh, online status, transaction history, or balance mutation. These operations must remain unsupported until the panel contract is captured.

## Contract limits

The captures contain successful examples only. Status/error response schemas, timeout behavior, rate limits, username/password validation, traffic units, Jalali date conversion, and idempotency behavior remain unverified. Client creation has not been called against the seller account as part of this code change.


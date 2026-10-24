# Trading Mandate foundation

A mandate is structured user intent, not a TradeIntent, strategy signal, Risk approval, order or trading permission. Saving, activating, pausing or retiring it has no portfolio, Risk, execution, provider or strategy side effects. ACTIVE means structurally ready only. No existing trading path reads mandates.

## Contract

PAPER, USDT, BTC/USDT spot and LONG_ONLY are the only supported environment/currency/market/direction. Required intent inputs are `capital_allocation` (positive finite exact decimal string), `risk_level` (CONSERVATIVE/BALANCED/GROWTH) and `trading_style` (SHORT_TERM/MEDIUM_TERM/LONGER_TERM). The fixed fields may be omitted or provided with their fixed values. Extra fields, including owner, status, quantity, market price, strategy parameters and trusted balances, are rejected. Allocation is an intention, not a cash reservation or proof of available capital. Multiple mandates may exist; no aggregate allocation or funding promise is made.

Risk level is a user preference, not a RiskConfiguration or authority to change platform limits. Trading style is not a raw timeframe. Future server-owned mapping to technical eligibility is deferred; CONSERVATIVE/GROWTH intent does not configure either profile for execution.

## API

All routes use existing cookie authentication and return no-store responses. Every write also requires the exact configured frontend Origin. Ownership always comes from the authenticated user; foreign and nonexistent IDs both return 404.

- `POST /api/mandates`: create a complete DRAFT (201).
- `GET /api/mandates`: list the user's mandates, newest first; empty list when none.
- `GET /api/mandates/{id}`: retrieve one owned mandate.
- `POST /api/mandates/{id}/intent`: replace all editable intent, supplying the same required fields as creation.
- `POST /api/mandates/{id}/activate`, `/pause`, `/retire`: lifecycle actions.

Example creation body: `{"capital_allocation":"2000.00","risk_level":"BALANCED","trading_style":"MEDIUM_TERM"}`.

Responses include mandate UUID, authenticated owner UUID, intent, status and explicit UTC created/updated timestamps. Financial values are decimal strings without binary float conversion or scale coercion. Validation errors return 422; invalid lifecycle or incomplete persisted intent returns 409. Database errors use the existing sanitized 503 handler.

## Lifecycle and persistence

DRAFT -> ACTIVE or RETIRED; ACTIVE -> PAUSED or RETIRED; PAUSED -> ACTIVE or RETIRED. RETIRED is terminal. Repeating the current lifecycle action is harmless. Intent edits are allowed only while DRAFT or PAUSED. Activation revalidates all persisted intent and the canonical active spot instrument. Drafts require complete intent in V1; partial drafting is deferred.

Row locks serialize edits and lifecycle actions. Ownership/identity/creation time are immutable. PostgreSQL enforces allowed values, positive finite allocation, mandatory fields, canonical market/owner foreign keys, lifecycle/edit rules and server timestamps. Hard deletion is refused; retire instead. This is a current-state intent record, not a complete edit-event audit log.

`0013_trading_mandates` adds one table, one owner/listing index and one lifecycle guard function/trigger. It changes no existing economic tables. Downgrade refuses while any mandate exists; it never deletes intent automatically. Migration deployment is explicit, never application startup. Source/offline DDL checks do not constitute real PostgreSQL verification; physical migration/trigger verification remains a separate isolated-database step before promotion. No migration is applied as part of implementation testing.

## Exclusions

No frontend, LLM input, LIVE mandates, SHORT, strategy selection, technical parameters, funding mutations, automatic observation, Risk evaluation, authorization, order creation, execution, billing or performance fees. Activation never starts trading.

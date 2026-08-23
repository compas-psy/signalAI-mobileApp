# T-Invest Sandbox Thin-Client Design

## Purpose

Move T-Invest Sandbox execution from the Android device to the SignalAI server so the phone is a true thin client and the user's VPN cannot affect broker connectivity. Acceptance is a provider-confirmed **round trip**: LIMIT BUY fill → LIMIT SELL fill → zero residual position. This change never enables T-Invest live execution.

## Security boundary

- Add a server-only encrypted integration slot `tinvest_sandbox_trade` with exactly one field: `token`.
- The token is write-only over the existing device-authenticated integrations API. GET exposes only metadata/configured state; server logs and provider errors never contain the token.
- Existing Android sandbox bearer is migrated once: read from Android Keystore, PUT to `tinvest_sandbox_trade`, verify the returned slot is configured, then delete the local sandbox token. If upload or confirmation fails, retain the local token and do not claim migration complete.
- If a configured server slot and a leftover local legacy token coexist, the local token is uploaded and exactly confirmed before local deletion; a configured server slot with no local token is already migrated and performs no unnecessary Keystore delete.
- After server migration, Android must not call T-Invest Sandbox directly for execution. It calls SignalAI server APIs only.
- Server sandbox transport has a compile-time fixed base URL `https://sandbox-invest-public-api.tbank.ru/rest`; callers cannot supply or switch to the live host.
- No live T-Invest credential, `tinvest_trade`, live host, CANARY/LIVE authority, or real account is used by the sandbox acceptance path.

## Provider contract

Use T-Invest HTTP Bearer authentication and the official sandbox methods. The acceptance path opens or reuses a dedicated named sandbox account derived from the scoped diagnostic identity and funds it with virtual RUB via `SandboxPayIn`.

The server selects the first currently API/limit-tradeable instrument from the compile-time allowlist `LQDT`, `TBRU`, `SBER`. A caller cannot supply a ticker. Selection uses `InstrumentsService.FindInstrument` and current `GetTradingStatus`.

Acceptance then performs two independent replay-safe legs:

1. **BUY** — one-lot crossing `ORDER_TYPE_LIMIT` at the current best ask, `TIME_IN_FORCE_FILL_AND_KILL`.
2. Require provider reconciliation through `GetSandboxOrderState` with `lotsExecuted > 0`.
3. **SELL** — exactly the executed BUY quantity as a crossing `ORDER_TYPE_LIMIT` at a fresh current best bid, also `TIME_IN_FORCE_FILL_AND_KILL`.
4. Require provider reconciliation with SELL executed lots equal to BUY executed lots.
5. Read `GetSandboxPositions` and require the tested instrument to have zero balance and zero blocked quantity.

An accepted, pending, rejected or zero-fill order is not success. A filled BUY with an unfilled SELL is not success. Two fills with a residual position are not success.

## Idempotency and isolation

The caller supplies only a bounded diagnostic key. The server scopes it to the **exact deployed source SHA and current `tinvest_sandbox_trade` credential generation** before deriving provider identities.

BUY and SELL have distinct stable provider request IDs. Every retry reconciles an existing leg before a possible submit, so a lost HTTP response cannot duplicate either leg. The dedicated named sandbox account prevents an unrelated historical sandbox holding from masquerading as, or blocking, this acceptance result.

A new deployed SHA or a rotated sandbox credential creates a new acceptance scope and invalidates the previous readiness proof.

## Server transport

`TInvestSandboxHttpTransport` implements the existing `TInvestTransport` protocol. It:

- accepts a token only from server secret loading;
- posts JSON only to the fixed sandbox REST namespace;
- uses bounded connect/read timeouts;
- converts HTTP/provider failures into bounded `TInvestProviderError` messages without response headers, token, or arbitrary raw response bodies;
- never exposes the bearer in `repr` or public return values.

The existing `TInvestAdapter(sandbox=True)` remains the execution semantics source for strategy execution. The transport/factory supplies its provider boundary.

## Thin-client migration

`TInvestSandboxAccess` migrates the legacy Android credential through `IntegrationsClient`:

1. read the local sandbox token, if one remains;
2. inspect whether the exact server slot is already configured;
3. if a local token exists, save it to `tinvest_sandbox_trade` even when that slot is already configured;
4. require exact server confirmation of slot/environment/fields/configured state;
5. only after confirmation delete the local sandbox token;
6. if no local token exists, use the exact server configured state without an unnecessary Keystore delete.

No secret is persisted in Dart files or returned by the server.

## Readiness proof and PAPER → SANDBOX

A successful round trip is persisted as append-only non-secret evidence containing source SHA, sandbox credential `updated_at` generation, instrument, sandbox account suffix, BUY/SELL provider order IDs and statuses, executed lots, flat-position result and observation time.

`PAPER → SANDBOX` is the only generic risk-increasing transition that may use this global provider acceptance proof without a strategy scope. The phone does **not** send a readiness boolean. When the owner taps SANDBOX while currently in PAPER, the thin client first invokes or reconciles the round-trip endpoint and only then asks the existing server promotion guard for a preview. The guard allows the transition only if an exact current release+credential proof exists. A separate confirmation tap still performs the actual mode mutation.

SANDBOX → CANARY and CANARY → LIVE retain their existing independent owner/security/performance gates. This slice grants them no authority.

## Samsung system inset

The execution-mode banner is outside `AppShell`, while `AppShell` already owns a `SafeArea`. The thin shell therefore consumes the top Android system inset exactly once around the banner, then removes that top padding before handing the remaining viewport to `AppShell`. This keeps the banner below Samsung status icons without double-padding the body or changing bottom-navigation inset handling.

## Sandbox API response

`POST /api/v1/tinvest-sandbox/smoke` remains device-authenticated and non-generic. It returns only sanitized acceptance evidence:

- `round_trip_complete`;
- symbol and sandbox account suffix;
- BUY provider order ID/status/executed lots;
- SELL provider order ID/status/executed lots;
- `position_flat`;
- non-secret readiness proof ID.

Broker credentials and raw provider responses are never returned.

## Real acceptance

This slice is complete only when:

1. exact-head server migrations/tests, Flutter analyze/tests and tracked-secret scan are green;
2. security diff review finds no reachable Critical/High issue;
3. the accepted exact SHA is merged and cumulatively deployed to the VPS and signed Android build;
4. the current sandbox credential exists on the server without being exposed;
5. a **real VPS-originated** T-Invest Sandbox run returns BUY `lotsExecuted > 0`, SELL executed lots equal to BUY lots, and `position_flat=true`;
6. that run persists the readiness proof for the deployed SHA + current credential generation;
7. the resulting build is physically checked on Samsung for the top-system-inset regression and `PAPER → SANDBOX` flow.

The phone's network/VPN is irrelevant to provider egress: broker traffic originates from the VPS.

## Non-goals

- No T-Invest live trading.
- No automatic CANARY/LIVE activation.
- No strategy, sizing, risk threshold, capital allocation, or kill-switch changes.
- No generic user-supplied broker URL or ticker.
- No weakening TLS verification.
- No claim that automated tests substitute for the real provider round trip or physical Samsung acceptance.
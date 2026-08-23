# T-Invest Sandbox Thin-Client Design

## Purpose

Move T-Invest Sandbox execution from the Android device to the SignalAI server so the phone is a true thin client and the user's Finland VPN cannot affect broker connectivity. Prove the path with one provider-confirmed sandbox fill. This change never enables T-Invest live execution.

## Security boundary

- Add a server-only encrypted integration slot `tinvest_sandbox_trade` with exactly one field: `token`.
- The token is write-only over the existing device-authenticated integrations API. GET exposes only metadata/configured state; server logs and provider errors never contain the token.
- Existing Android sandbox bearer is migrated once: read from Android Keystore, PUT to `tinvest_sandbox_trade`, verify the returned slot is configured, then delete the local sandbox token. If upload or confirmation fails, retain the local token and do not claim migration complete.
- If a configured server slot and a leftover local legacy token coexist, the local token is uploaded and exactly confirmed before local deletion; a configured server slot with no local token is already migrated and performs no unnecessary Keystore delete.
- After server migration, Android must not call T-Invest Sandbox directly for execution. It calls SignalAI server APIs only.
- Server sandbox transport has a compile-time fixed base URL `https://sandbox-invest-public-api.tbank.ru/rest`; callers cannot supply or switch to the live host.
- No live T-Invest credential, `tinvest_trade`, live host, owner promotion state, execution mode promotion, or real account is used by the smoke path.

## Provider contract

Use T-Invest HTTP Bearer authentication and the official `SandboxService` methods. The smoke path may open/reuse a sandbox account and fund it with virtual RUB via `SandboxPayIn`. It submits one BUY with a stable idempotency request id, then reads `GetSandboxOrderState`. Success means provider evidence reports `lotsExecuted > 0`; an accepted but unfilled order is not success.

Instrument selection is diagnostic and provider-driven rather than tied to one ticker. The server uses a compile-time allowlist (`LQDT`, `TBRU`, `SBER` fallback), resolves each candidate through `InstrumentsService.FindInstrument` with `apiTradeAvailableFlag=true`, and selects the first candidate whose current `GetTradingStatus` supports an API market order or a crossing limit order. A caller cannot supply an arbitrary ticker.

## Server transport

Create a narrow `TInvestSandboxHttpTransport` implementing the existing `TInvestTransport` protocol. It:

- accepts a token only from server secret loading;
- posts JSON only to the fixed sandbox REST namespace;
- uses bounded connect/read timeouts;
- converts HTTP/provider failures into bounded `TInvestProviderError` messages without response headers, token, or arbitrary raw response bodies;
- never exposes the bearer in `repr` or public return values.

The existing `TInvestAdapter(sandbox=True)` remains the execution semantics source for strategy execution. The new transport/factory supplies its missing provider boundary.

## Thin-client migration

`TInvestSandboxAccess` gains a server migration operation using `IntegrationsClient`:

1. read the local sandbox token, if one remains;
2. inspect whether the exact server slot is already configured;
3. if a local token exists, save it to server slot `tinvest_sandbox_trade` even when that slot is already configured;
4. require returned `slot == tinvest_sandbox_trade`, `environment == sandbox`, exact field set, and `configured == true`;
5. only after that exact confirmation delete the local sandbox token;
6. if no local token exists, return the exact server configured state without issuing a local delete.

No secret is persisted in Dart files or returned by the server.

## Sandbox execution route

Add a device-authenticated sandbox-only smoke endpoint under `/api/v1/tinvest-sandbox`. It is deliberately not a generic order endpoint. It performs a single bounded diagnostic BUY using the server vault, fixed sandbox host, fixed diagnostic instrument allowlist, and current provider trading status, then provider reconciliation. The endpoint returns only non-secret evidence: symbol/instrument, sandbox account suffix, provider order id, execution status, executed lots and a boolean `filled`.

The request is idempotent for one supplied diagnostic key. Replays reconcile the same provider request id rather than submit a duplicate order.

## Real acceptance

The slice is accepted only when all automated tests and project Quality Gate are green, security review has no Critical/High finding, the server code is deployed from the accepted exact SHA, the existing phone token has migrated to the server (or the slot was already configured), and a real call from the SignalAI server receives T-Invest Sandbox evidence with `lotsExecuted > 0`.

The phone's VPN location is intentionally irrelevant: broker egress originates from the VPS.

## Non-goals

- No T-Invest live trading.
- No CANARY/LIVE promotion changes.
- No strategy, sizing, risk threshold, capital allocation, or kill-switch changes.
- No generic user-supplied broker URL or ticker.
- No weakening TLS verification.
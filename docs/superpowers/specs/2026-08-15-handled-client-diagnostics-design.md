# Handled client diagnostics — P0 observability slice

Issue: #39.

## Goal

Make the two user-visible silent degradation paths diagnosable without changing their non-fatal UX semantics: full idea hydration failure and chart fallback exhaustion.

## Scope

- `EngineClient.detail` continues returning `null` on a failed full-card hydration, but emits one structured handled-failure callback with the original error and stack.
- `barsWithFallback` continues trying the deterministic setup → 4h → 1h → 1d chain and emits one handled-failure only after the complete chain fails.
- A successful detail or chart load emits no diagnostic event.
- Chart diagnostic text contains attempted timeframes/reasons but deliberately does not add the instrument ID.
- Production thin mode routes these callbacks into the existing shared `RuntimeErrorRecorder`; credential redaction therefore happens before local persistence exactly as for crash/error events.

## Boundaries

`EngineClient` exposes neutral `EngineFailureStage` / `EngineHandledFailure` types and an optional reporter. It does not import the monitor layer. The app bootstrap maps stages to `RuntimeErrorKind` and records them with the same recorder instance that owns global Flutter/async errors.

No new telemetry service, database, credential handling, trade execution, strategy or broker behavior is introduced.

## Verification

Tests first prove: detail stays non-fatal but reports once; chart exhaustion reports exactly once after all fallback timeframes; chart diagnostic does not include instrument ID; successful loads emit nothing. Existing runtime-recorder redaction tests remain the persistence/secret-safety contract. Exact-head Quality Gate must pass before merge.
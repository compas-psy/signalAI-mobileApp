# Sandbox reconciliation diagnostics — final #39 slice

Issue: #39.

## Goal

Make sandbox mirroring/reconciliation failures visible in the same bounded, secret-safe device-local runtime history used for crashes, idea hydration and chart failures.

## Scope

- Add `sandboxReconciliation` as a handled engine/runtime diagnostic kind.
- Any sandbox result with failure tone emits one handled diagnostic before the existing toast is queued.
- Warning/success results remain user-visible only and do not add error noise.
- Reuse the existing `RuntimeErrorRecorder`; all messages pass through its credential redaction before persistence.
- Add a small aggregate summary over the retained bounded history: total, count by kind, latest timestamp.
- Keep sandbox order IDs, stable idempotency keys, provider reconciliation algorithm and broker side effects unchanged.

## Verification

Tests first prove that durable-state refusal produces a sandbox reconciliation diagnostic while still returning the accepted server paper decision, and that local summary counts retained events by kind. Exact-head Flutter/server quality gate must pass before merge.
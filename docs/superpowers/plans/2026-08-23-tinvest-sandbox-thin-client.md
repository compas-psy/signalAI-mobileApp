# T-Invest Sandbox Thin-Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move T-Invest Sandbox credentials and execution to the SignalAI server and prove a real provider-confirmed LIMIT BUY → LIMIT SELL → flat-position round trip.

**Architecture:** Reuse the encrypted integration vault and existing sandbox provider boundary. Keep a fixed-host server transport and write-only sandbox credential, migrate the legacy Android Keystore credential once, then make technical Sandbox readiness depend on append-only provider evidence bound to the exact deployed SHA and current credential generation. Live T-Invest execution remains unreachable.

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL/pgcrypto, Python stdlib HTTP/TLS, Flutter/Dart, Android Keystore, T-Invest REST SandboxService.

**Spec:** `docs/superpowers/specs/2026-08-23-tinvest-sandbox-thin-client-design.md`

## Global Constraints

- TDD: tests first and observed RED before production code.
- Fixed provider host: `https://sandbox-invest-public-api.tbank.ru/rest` only.
- No token in git, responses, logs, exceptions, repr or telemetry.
- No `tinvest_trade`, live host, live account, CANARY or LIVE authority.
- Money-path requests are idempotent and reconcile before submit.
- Round-trip success requires BUY executed lots > 0, SELL executed lots == BUY lots, and provider position flat.
- Automated green tests are not a substitute for real VPS provider evidence or physical Samsung acceptance.

---

### Task 1: Server-owned sandbox credential — COMPLETE

**Files:**
- Modify: `server/app/integration_secrets.py`
- Test: `server/tests/test_integrations_api.py`

- [x] Add a failing API test proving the slot exists, is write-only, is not a live-step-up slot, accepts exactly one non-empty token, and GET never returns its value.
- [x] Observe RED.
- [x] Add `tinvest_sandbox_trade`, venue `TINVEST`, environment `sandbox`, field `token`.
- [x] Run focused test GREEN.

### Task 2: Fixed-host T-Invest sandbox transport — COMPLETE

**Files:**
- `server/app/execution/venues/tinvest_transport.py`
- `server/tests/execution/venues/test_tinvest_sandbox_transport.py`

- [x] Add RED tests for fixed sandbox URL, Bearer JSON request, bounded errors, missing credential fail-before-network, secret-free repr/errors and malformed provider paths.
- [x] Observe RED.
- [x] Implement standard-library HTTPS transport with fixed sandbox host.
- [x] Run focused tests GREEN.

### Task 3: One-time phone → server credential migration — COMPLETE

**Files:**
- `lib/data/api/sandbox_mirroring_engine_client.dart`
- `test/tinvest_sandbox_thin_client_migration_test.dart`

- [x] Test successful upload/delete, failed upload retaining local token, malformed confirmation retaining token, configured-server overwrite from leftover local token, and replay with no local token/no delete.
- [x] Observe Flutter RED.
- [x] Implement migration without local-file secret persistence.
- [x] Remove direct broker execution fallback from Android thin client.
- [x] Run Flutter analyze/tests GREEN.

### Task 4: Initial server smoke and provider-driven instrument selection — COMPLETE, SUPERSEDED BY ROUND TRIP

**Files:**
- `server/app/execution/venues/tinvest_sandbox_smoke.py`
- `server/app/api/v1/tinvest_sandbox.py`
- server tests

- [x] Add server-owned smoke route and sanitized evidence response.
- [x] Add provider-driven candidate selection `LQDT` → `TBRU` → `SBER` fallback.
- [x] Require current API trading availability instead of hard-coding SBER availability.
- [x] Preserve reconcile-before-submit idempotency.
- [x] Pass exact-head Quality Gate for the initial BUY-only implementation.
- [x] Record that BUY-only acceptance is insufficient and must not be represented as completed RoundTrip.

### Task 5: Real round trip + PAPER → SANDBOX readiness — IN PROGRESS

**Files:**
- Modify: `server/app/execution/venues/tinvest_sandbox_smoke.py`
- Create: `server/app/execution/venues/tinvest_sandbox_readiness.py`
- Create: `server/app/models/tinvest_sandbox.py`
- Create: `server/alembic/versions/0044_tinvest_sandbox_roundtrip.py`
- Modify: `server/app/api/v1/tinvest_sandbox.py`
- Modify: `server/app/execution/promotion_guard.py`
- Modify: `lib/state/execution_mode_controller.dart`
- Modify: `lib/ui/execution_mode_shell.dart`
- Tests: server + Flutter round-trip/readiness/system-inset tests

- [x] Write RED tests for LIMIT BUY → provider fill → LIMIT SELL → provider fill → flat position.
- [x] Write RED replay tests proving neither BUY nor SELL is duplicated.
- [x] Write RED tests proving an unfilled BUY never submits SELL, an unfilled SELL is failure, and residual position is failure.
- [x] Write RED tests binding readiness to exact source SHA + current sandbox credential generation.
- [x] Write RED test for Samsung top inset consumed exactly once.
- [x] Observe expected RED in CI before implementation.
- [x] Implement a dedicated scoped sandbox account and two stable provider leg identities.
- [x] Force both acceptance legs to crossing LIMIT + FILL_AND_KILL semantics.
- [x] Persist append-only non-secret round-trip proof only after both fills and flat position.
- [x] Make `PAPER → SANDBOX` preview consume only current server proof; no client readiness boolean.
- [x] Make the phone invoke/reconcile the server round trip before requesting `PAPER → SANDBOX` preview.
- [x] Fix execution-mode banner top inset without double-padding `AppShell`.
- [ ] Pass final exact-head server + Flutter Quality Gate after all follow-up corrections and docs.
- [ ] Complete security diff review with no reachable Critical/High finding.

### Task 6: Merge, cumulative release and **real** VPS RoundTrip — PENDING

- [ ] Merge only the final GREEN exact head.
- [ ] Trigger one cumulative release from the merged exact default SHA.
- [ ] Confirm VPS deploy reports the same exact source SHA.
- [ ] Confirm `tinvest_sandbox_trade` is configured without printing/returning its value. If absent, migrate it from the signed updated Android app; never paste the token into chat.
- [ ] Run VPS T-Invest Sandbox acceptance using stable release+credential-scoped idempotency.
- [ ] Require provider-confirmed LIMIT BUY `lotsExecuted > 0`.
- [ ] Require provider-confirmed LIMIT SELL with executed lots equal to BUY lots.
- [ ] Require `GetSandboxPositions` to show zero balance and zero blocked quantity for the tested instrument.
- [ ] Require persisted readiness proof to match deployed source SHA + current credential generation.
- [ ] Verify `PAPER → SANDBOX` preview becomes allowed from that proof and still requires explicit confirmation for the mode mutation.
- [ ] Install/use the new signed APK on Samsung and physically verify the execution banner no longer overlaps the system status area.
- [ ] Record physical result in the device acceptance issue.

### Task 7: Return to the remaining platform plan — PENDING / OWNER-GATED WHERE NOTED

- [ ] Reconcile SAI-084–090 issue state after the Sandbox acceptance slice.
- [ ] Keep SAI-084 CANARY activation blocked until owner/security decisions are explicitly resolved; do not infer authority from Sandbox completion.
- [ ] Continue SAI-085 Canary proof acceptance criteria only after SAI-084 prerequisites are resolved.
- [ ] Close SAI-086 only after current Samsung physical acceptance evidence is recorded.
- [ ] Mark SAI-087 cumulative release only from the actual accepted exact SHA.
- [ ] Do not implement/activate SAI-088 scaled LIVE without the required owner ladder/evidence.
- [ ] Do not invent SAI-089 microstructure thresholds.
- [ ] Do not grant SAI-090 ML challenger decision authority without incremental OOS proof.

## RoundTrip DoD

RoundTrip is **finished only if all of these are simultaneously true** for the currently deployed build and current sandbox credential:

1. a currently allowed diagnostic instrument is selected from the fixed server allowlist;
2. a LIMIT BUY is provider-confirmed with executed lots > 0;
3. a LIMIT SELL is provider-confirmed for exactly the BUY executed quantity;
4. provider positions confirm zero residual/blocked quantity for that instrument;
5. the non-secret proof is persisted against the exact deployed SHA + credential generation;
6. replay of the same acceptance identity reconciles rather than duplicates orders;
7. the server promotion guard recognizes that exact proof for `PAPER → SANDBOX`.

Until all seven are evidenced in the real VPS/provider path, the correct status is **RoundTrip NOT FINISHED**, regardless of automated test results.
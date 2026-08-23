# T-Invest Sandbox Thin-Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move T-Invest Sandbox credentials and execution to the SignalAI server and prove one real provider-confirmed sandbox fill.

**Architecture:** Reuse the encrypted integration vault and existing `TInvestAdapter(sandbox=True)`. Add a fixed-host server HTTP transport, a write-only sandbox credential slot, an idempotent sandbox-only smoke service/API, and one-time Android Keystore → server migration. Live T-Invest execution remains unreachable.

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL/pgcrypto, Python stdlib HTTP/TLS, Flutter/Dart, Android Keystore, T-Invest REST SandboxService.

**Spec:** `docs/superpowers/specs/2026-08-23-tinvest-sandbox-thin-client-design.md`

## Global Constraints

- TDD: tests first and observed RED before production code.
- Fixed provider host: `https://sandbox-invest-public-api.tbank.ru/rest` only.
- No token in git, responses, logs, exceptions, repr or telemetry.
- No `tinvest_trade`, live host, live account or promotion change.
- Money-path requests are idempotent and reconciled.
- Real acceptance requires `lotsExecuted > 0` from T-Invest Sandbox.

---

### Task 1: Server-owned sandbox credential

**Files:**
- Modify: `server/app/integration_secrets.py`
- Test: `server/tests/test_integrations_api.py`

**Interfaces:**
- Produces integration slot `tinvest_sandbox_trade`, venue `TINVEST`, environment `sandbox`, field `token`.

- [ ] Add a failing API test proving the slot exists, is write-only, is not a live-step-up slot, accepts exactly one non-empty token, and GET never returns its value.
- [ ] Run the focused test and retain RED.
- [ ] Add the minimal `IntegrationSpec` and update the obsolete device-owned-sandbox documentation.
- [ ] Run focused test GREEN and commit.

### Task 2: Fixed-host T-Invest sandbox transport

**Files:**
- Create: `server/app/execution/venues/tinvest_transport.py`
- Test: `server/tests/execution/venues/test_tinvest_sandbox_transport.py`

**Interfaces:**
- Produces `TInvestSandboxHttpTransport(token)` implementing `TInvestTransport.call(service, method, body)`.
- Produces `build_tinvest_sandbox_transport(db)` loading only `tinvest_sandbox_trade`.

- [ ] Add failing tests for fixed sandbox URL, Bearer JSON request, bounded errors, missing credential fail-before-network, secret-free repr/errors, and rejection of malformed service/method names.
- [ ] Run focused tests and retain RED.
- [ ] Implement with standard-library HTTPS so production dependencies do not change.
- [ ] Run focused tests GREEN and commit.

### Task 3: Idempotent real-fill smoke service/API

**Files:**
- Create: `server/app/execution/venues/tinvest_sandbox_smoke.py`
- Create: `server/app/api/v1/tinvest_sandbox.py`
- Modify: `server/app/main.py`
- Test: `server/tests/execution/venues/test_tinvest_sandbox_smoke.py`
- Test: `server/tests/test_tinvest_sandbox_api.py`

**Interfaces:**
- `run_tinvest_sandbox_smoke(db, diagnostic_key)` uses only the server sandbox transport.
- `POST /api/v1/tinvest-sandbox/smoke` accepts a bounded idempotency key and returns sanitized provider evidence.

- [ ] Add RED tests proving missing credentials fail before provider I/O, duplicate diagnostic keys reconcile rather than duplicate, only SandboxService is used for account/pay-in/order state, and `filled` is true only when `lotsExecuted > 0`.
- [ ] Add API RED tests proving device authentication remains required and no credential/provider raw body is returned.
- [ ] Implement minimal account reuse/open, virtual RUB pay-in, `FindInstrument` + current trading-status selection from a fixed diagnostic allowlist (`LQDT`, `TBRU`, `SBER` fallback), market-or-crossing-limit BUY of one lot, and final order reconciliation.
- [ ] Run focused tests GREEN and commit.

### Task 4: One-time phone → server credential migration

**Files:**
- Modify: `lib/data/api/sandbox_mirroring_engine_client.dart`
- Test: `test/tinvest_sandbox_thin_client_migration_test.dart`

**Interfaces:**
- `TInvestSandboxAccess.migrateToServer(IntegrationsClient client)` uploads an existing Keystore bearer to `tinvest_sandbox_trade` and deletes the local token only after exact server confirmation.
- If the exact server slot already exists but a local legacy token remains, the local value is uploaded first and only then deleted; if no local token remains, the configured server slot is already migrated and no local delete is performed.
- `TInvestSandboxAccess.configured()` reflects server-owned state after migration where a client is available.

- [ ] Add RED tests for successful upload/delete, failed upload retaining local token, malformed server confirmation retaining token, configured-server overwrite from a leftover local token, and configured-server replay with no local token/no delete.
- [ ] Run Flutter tests RED.
- [ ] Implement migration with no local-file secret persistence and no broker call.
- [ ] Remove direct phone broker execution from `SandboxMirroringEngineClient`; the thin client delegates sandbox mirroring/smoke to SignalAI server or reports server migration required.
- [ ] Run Flutter analyze/tests GREEN and commit.

### Task 5: Exact-head security and deployment acceptance

**Files:**
- Update docs only for factual corrections.

- [ ] Run full exact-head Quality Gate: server imports/migrations/pytest, Flutter analyze/test, tracked-secret scan.
- [ ] Security-diff review every changed source file; confirm fixed sandbox host, write-only credential, no live reachability, no secret leakage, idempotent order identity and reconciliation.
- [ ] Merge only with GREEN exact-head evidence and no Critical/High finding.
- [ ] Trigger one cumulative deployment from the accepted SHA, not per intermediate commit.
- [ ] On VPS, verify `tinvest_sandbox_trade` configured without printing its value. If not yet configured, migrate it from the updated Android app; do not ask the owner to paste the token into chat.
- [ ] Invoke the server smoke endpoint and require T-Invest `GetSandboxOrderState` evidence `lotsExecuted > 0` before declaring the sandbox path working.
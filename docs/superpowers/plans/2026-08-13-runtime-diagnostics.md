# Runtime Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and verification-before-completion. Keep this slice server-only.

**Goal:** Add request correlation and an authenticated aggregate runtime snapshot without changing trading behavior or database schema.

**Architecture:** A thin outer request-ID middleware wraps the existing authentication boundary. A new diagnostics router reads aggregate counts/timestamps from existing models through the normal SQLAlchemy session. No telemetry persistence is added.

**Tech Stack:** FastAPI/Starlette, SQLAlchemy, PostgreSQL, pytest.

## Global Constraints

- No new migration or dependency.
- No credentials, notification payloads, instruments or market snapshots in diagnostics.
- Existing `DeviceTokenMiddleware` remains the business-API authorization boundary.
- No trading/risk/sizing/execution behavior changes.

---

### Task 1: RED contract tests

**Files:**
- Create: `server/tests/test_runtime_diagnostics.py`

- [ ] Test `/health` returns a valid UUID `X-Request-ID`.
- [ ] Test a valid incoming UUID is echoed unchanged.
- [ ] Test unauthorized `/api/v1/diagnostics/runtime` still returns `X-Request-ID`.
- [ ] Test authorized diagnostics endpoint returns the documented aggregate empty-state contract.
- [ ] Open draft PR and verify the exact-head Quality Gate fails because the feature is absent.

### Task 2: Request correlation middleware

**Files:**
- Create: `server/app/request_context.py`
- Modify: `server/app/main.py`

- [ ] Implement UUID validation/generation without reading request body or credentials.
- [ ] Store ID on `request.state.request_id`.
- [ ] Add `X-Request-ID` to every response.
- [ ] Register it outside existing middlewares.

### Task 3: Runtime diagnostics endpoint

**Files:**
- Create: `server/app/api/v1/diagnostics.py`
- Modify: `server/app/main.py`

- [ ] Aggregate ideas by status and latest signal time.
- [ ] Aggregate paper trades by status, live count, unreconciled live count and oldest live reconciliation time.
- [ ] Aggregate notification outbox total/latest ID/latest timestamp.
- [ ] Return only aggregate values and request ID.

### Task 4: GREEN verification

- [ ] Exact-head Quality Gate: server PostgreSQL tests, migrations/imports, Flutter analyze/tests, tracked secret scan.
- [ ] Inspect PR diff: only diagnostics/request-context/tests/router wiring/docs.
- [ ] Merge only when exact-head gate is green.
- [ ] Do not trigger cumulative production release as part of merge.
# Owner Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a transparent FORTS/Bybit owner control plane and restore honest Bybit crypto-carry Shadow measurement.

**Architecture:** Keep the trading lifecycle untouched. Add a read-only aggregation service/API over existing TradeIdea, Shadow, Paper A/B, BacktestRun and ModelRegistry evidence, and expose it through one thin-client Settings screen. Fix crypto-carry input availability at the Shadow supplemental-facts boundary using the existing public Bybit adapter plus explicit config-owned cost assumptions.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy / pytest; Flutter / Dart widget tests; existing Bybit v5 public adapter.

**Spec:** `docs/OWNER_CONTROL_PLANE_V1.md`

## Global Constraints

- Shadow must never create `TradeIdea`, `PaperTrade`, notifications or execution intents.
- Existing owner/control scan runs before experimental measurement and must remain unchanged.
- Missing inputs/outcomes are explicit; never coerce them to zero-return evidence.
- Mobile v1 is read-only: no promotion, parameter editor or trading mutation.
- Absolute risk caps remain outside optimizer control.
- All numeric carry cost assumptions come from config; no hidden fallback constants.

---

### Task 1: Bybit crypto-carry Shadow facts

**Files:**
- Modify: `server/app/shadow/collector_v1.py`
- Modify: `server/config/default.yaml`
- Test: `server/tests/shadow/test_shadow_collector_v1.py`

**Interfaces:**
- Consumes: existing `app.market.crypto.carry_market_facts(symbol, evaluated_at=...)`.
- Produces: default `ShadowSupplementalFacts` with real `crypto_carry_facts` for crypto instruments, or a stable unavailable reason.

- [ ] Add failing tests proving the default provider calls the Bybit carry resolver for crypto, uses config-owned costs, and maps resolver failure to `INPUT_UNAVAILABLE`.
- [ ] Run the targeted Shadow tests in CI and verify RED because current `_metadata_facts` always returns `crypto_carry_facts=None`.
- [ ] Implement a default facts provider that layers live carry facts over metadata-only bar/cost context for crypto while preserving fail-closed behavior.
- [ ] Add explicit `shadow.crypto_carry` cost assumptions to `default.yaml` and include them in the Shadow cost-model identity.
- [ ] Run Shadow tests and verify GREEN.

### Task 2: Read-only control dashboard aggregation

**Files:**
- Create: `server/app/control/dashboard.py`
- Create: `server/app/api/v1/control.py`
- Modify: API router registration file(s)
- Test: `server/tests/control/test_control_dashboard.py`
- Test: `server/tests/api/test_control_api.py` or nearest existing API-test location

**Interfaces:**
- Produces: `build_control_dashboard(session, venue, window_hours, now)` and `GET /api/v1/control/dashboard`.

- [ ] Add failing service tests for empty DB, venue filtering, broken-input classification, pending Paper A/B semantics, backtest selection and risk optimizer snapshot.
- [ ] Verify RED.
- [ ] Implement deterministic aggregation helpers and verdict logic.
- [ ] Add the FastAPI read-only route with strict `FORTS|BYBIT` validation and bounded `window_hours`.
- [ ] Verify targeted server tests GREEN.

### Task 3: Thin-client Control screen

**Files:**
- Create: `lib/ui/screens/server_control_screen.dart`
- Modify: `lib/state/navigation.dart`
- Modify: `lib/ui/app_shell.dart`
- Test: `test/ui/server_control_screen_test.dart`
- Test: navigation/shell tests that assert visible Settings pill order

**Interfaces:**
- Consumes: `ApiClient.get('/api/v1/control/dashboard?...')`.
- Produces: read-only `Контроль` screen with FORTS/BYBIT switch and four sections: Сейчас, Competition, Backtest/OOS, Risk optimizer.

- [ ] Add failing widget/navigation tests for the new Settings pill, venue switch, BROKEN INPUT visibility and null-safe metric rendering.
- [ ] Verify RED in Flutter CI.
- [ ] Implement the screen using existing `SectionCard`, badges, segmented/tile primitives and `ApiClient` injection for tests.
- [ ] Wire `SettingsPill.control` into thin navigation without changing top-level five-section navigation.
- [ ] Verify widget/navigation tests GREEN.

### Task 4: Regression and release evidence

**Files:**
- Modify documentation only if contract details changed during implementation.

**Interfaces:**
- Produces: CI evidence and PR against `claude/release-y40hk5`.

- [ ] Run targeted Python and Flutter suites.
- [ ] Run repository quality gate.
- [ ] Inspect diff for accidental changes to owner scan/admission/risk/execution thresholds.
- [ ] Open PR with explicit P0 Bybit fix, API/UI screenshots-by-contract description, test evidence and deployment note.

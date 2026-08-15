# SPREAD Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert persisted, decision-visible Rosstat producer-price observations into the existing SPREAD engine and common research pipeline without partial baskets or invented issuer exposure.

**Architecture:** Add one offline runtime module. Configuration is explicit and injectable; the production basket registry stays empty until a post-green live-source probe validates exact current series. The runtime queries only configured machine keys, builds complete periods, calls `engines.spread.evaluate()`, and can create pipeline signals only for explicitly configured issuers.

**Tech Stack:** Python 3, SQLAlchemy, existing research models/engine/fusion/pipeline, pytest.

## Global Constraints

- Do not add another SPREAD engine.
- Do not add a network fetch or third-party dependency.
- Never use an observation before persisted `tradable_at`.
- Never substitute zero, forward-fill, or silently drop a required leg into a partial optimistic period.
- Never infer issuer exposure from labels; production registry remains empty until live validation.
- Do not modify trading/execution behavior.

---

### Task 1: Runtime preparation contract

**Files:**
- Create: `server/tests/test_spread_runtime.py`
- Create: `server/app/research/spread_runtime.py`

**Interfaces:**
- Produces: `SpreadLegConfig`, `SpreadBasket`, `SpreadPreparation`, `prepare_periods(session, basket, as_of)`.
- `SpreadLegConfig` fields: `observation_type`, `unit`, `coefficient`, `rationale`, `side` (`product` or `input`).
- `SpreadBasket` fields: `basket_id`, `issuers`, `legs`, `revenue_coverage`, `cost_coverage`, `calibrated`, `contract_lag_periods`, `hedged`, `vertically_integrated`.
- `SpreadPreparation` fields: `periods`, `observation_ids`, `reason_codes`.

- [ ] **Step 1: Write failing tests for visibility, completeness and unit safety**

Create fixture observations with exact configured machine keys. Assert:

```python
prepared = prepare_periods(session, basket, as_of=cutoff)
assert [period.period for period in prepared.periods] == expected_complete_periods
assert "spread_observation_not_yet_tradable" in prepared.reason_codes
```

Also assert a wrong `OKEI:<code>` unit yields no prepared period and includes `spread_unit_mismatch`, and that a month missing one required leg is never materialized as a `spread.Period`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `pytest server/tests/test_spread_runtime.py -q`
Expected: FAIL because `app.research.spread_runtime` does not exist.

- [ ] **Step 3: Implement minimal preparation code**

Use an exact SQL query:

```python
select(ResearchObservation).where(
    ResearchObservation.source_id == rosstat_prices.SOURCE_ID,
    ResearchObservation.observation_type.in_(required_types),
    ResearchObservation.tradable_at <= as_of,
)
```

Validate unit before grouping. For each `period_end`, materialize a `spread.Period` only when every configured leg has one usable numeric observation. Map configured `side` to `products` or `inputs`; copy the configured coefficient and the stored numeric value into `spread.Leg`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `pytest server/tests/test_spread_runtime.py -q`
Expected: PASS.

### Task 2: Evaluation and common-pipeline handoff

**Files:**
- Modify: `server/tests/test_spread_runtime.py`
- Modify: `server/app/research/spread_runtime.py`

**Interfaces:**
- Produces: `SpreadRunReport`, `run_spread(session, baskets=(), now=None)`.

- [ ] **Step 1: Add failing tests for fail-closed no-signal and explicit issuer handoff**

Assert:

```python
report = run_spread(session, baskets=(), now=now)
assert report.signals == 0
assert report.hypotheses == 0
assert "SPREAD: production baskets not configured" in report.skipped
```

For an injected fixture basket with explicit `issuers=("GAZP",)`, 12 complete visible periods and a persistent positive spread, assert a SPREAD signal/hypothesis is produced only for `GAZP`. Remove one required series from enough months and assert no signal plus an explicit insufficient-complete-history reason.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `pytest server/tests/test_spread_runtime.py -q`
Expected: FAIL because `run_spread` is absent.

- [ ] **Step 3: Implement minimal evaluation/pipeline code**

For each basket:

```python
result = spread.evaluate(
    prepared.periods,
    revenue_coverage=basket.revenue_coverage,
    cost_coverage=basket.cost_coverage,
    calibrated=basket.calibrated,
    contract_lag_periods=basket.contract_lag_periods,
    hedged=basket.hedged,
    vertically_integrated=basket.vertically_integrated,
)
```

If inapplicable or neutral, record its exact reason/detail and emit nothing. Otherwise create `SignalInput(strategy_key="SPREAD", ...)` only for `basket.issuers`. Resolve issuer confidence through the existing issuer registry and add `for_hypothesis(...)` as a market-context overlay. Call the existing `run_pipeline`; do not write hypotheses directly.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `pytest server/tests/test_spread_runtime.py -q`
Expected: PASS.

### Task 3: Regression and exact-head gate

**Files:**
- No additional production files unless a regression reveals a directly related defect.

- [ ] **Step 1: Run relevant research suites**

Run:

```bash
pytest server/tests/test_spread_runtime.py server/tests/test_rosstat_prices_collector.py server/tests/test_rosstat_prices_workbook.py server/tests/test_research_engines_more.py -q
```

Expected: PASS.

- [ ] **Step 2: Run repository Quality Gate on the exact PR head**

Expected: server/PostgreSQL tests, Flutter tests, secret scan and release-attestation guard all GREEN for the same commit SHA.

- [ ] **Step 3: Review the PR diff**

Expected changed implementation scope: one new runtime module, one new test module, and the design/plan docs. No trading/execution files and no default live issuer basket.

- [ ] **Step 4: Merge only the exact green head**

After merge, perform the live Rosstat probe as a separate follow-up before configuring the first production sector basket.
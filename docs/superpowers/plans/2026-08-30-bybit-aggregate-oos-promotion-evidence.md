# Bybit aggregate OOS + promotion evidence implementation plan

Spec: `docs/superpowers/specs/2026-08-30-bybit-aggregate-oos-promotion-evidence-design.md`

## Task 1 — Characterize current aggregate-gate bug (RED)

Files:
- `server/app/backtest/bybit_entry_backtest.py`
- `server/tests/test_bybit_entry_backtest.py`
- `server/config/default.yaml`

Steps:
1. Add a regression test proving a per-symbol run with fewer than `min_aggregate_trades` must remain diagnostic/FAIL and cannot stand in for the cross-symbol aggregate gate.
2. Add a test fixture with multiple immutable DATA_READY snapshots whose pooled OOS trades exceed the aggregate threshold while no individual symbol does.
3. Run the targeted test and record the expected failure before production code changes.

## Task 2 — Extract replay evidence without changing per-symbol semantics

Files:
- `server/app/backtest/bybit_entry_backtest.py`
- `server/tests/test_bybit_entry_backtest.py`

Steps:
1. Refactor the existing PIT-safe purged walk-forward evaluator into an internal replay result that exposes exact `_RTrade` evidence plus fold evidence.
2. Keep existing per-symbol `BacktestRun` output/labels/metrics behavior compatible.
3. Run existing and new entry-backtest tests.

## Task 3 — Implement deterministic cross-symbol aggregate OOS (GREEN)

Files:
- `server/app/backtest/bybit_aggregate_backtest.py` (new)
- `server/app/backtest/bybit_research_runtime.py`
- `server/tests/test_bybit_aggregate_backtest.py` (new)
- `server/tests/test_bybit_research_runtime.py`

Steps:
1. Resolve the latest DATA_READY immutable snapshot for each eligible Bybit perpetual.
2. Replay each snapshot with the same strategy evaluator, cost model and walk-forward contract.
3. Pool exact OOS trades; never average per-symbol summary metrics.
4. Compute deterministic aggregate expectancy, PF, max-DD R, top-5 contribution, Sharpe/Sortino and trade counts.
5. Address the aggregate run by ordered snapshot-set + strategy/config/engine identity; make duplicate execution idempotent.
6. Enforce `min_aggregate_trades` only here; retain per-symbol gates as diagnostics.
7. Wire aggregate research into the heavy research lifecycle without publishing trade ideas.
8. Run targeted aggregate/runtime tests.

## Task 4 — Prove Paper A/B evidence root cause (RED)

Files to trace after inspection:
- Paper A/B decision/outcome implementation
- promotion evidence query/registry implementation
- scheduler integration
- corresponding tests

Steps:
1. Trace `shadow -> decision -> immediate/deferred label -> persistence -> promotion scope` end-to-end.
2. Add the smallest failing test reproducing the production symptom: labeled A/B work exists but promotion evidence sees zero durable outcomes/scopes.
3. Confirm failure reason before changing production code.

## Task 5 — Repair Paper A/B → promotion evidence (GREEN)

Steps:
1. Make labeled candidate/control outcomes durable and exactly-once in the representation consumed by promotion evidence, or make promotion evidence consume the already-durable canonical representation if duplication is the bug.
2. Preserve replay/idempotency and shadow/paper-only constraints.
3. Add tests for immediate labels, deferred labels, duplicate reruns and empty/blocked cases.
4. Assert no automatic live generator mutation occurs.

## Task 6 — Control/API/UI observability

Files to inspect/modify only as required:
- `server/app/control/...`
- Flutter control models/widgets and tests

Steps:
1. Surface aggregate OOS separately from per-symbol OOS, including symbols, N, E[R], PF, MaxDD R, gate status and blockers.
2. Surface Paper A/B evidence counts/scopes so strategy competition is auditable.
3. Keep explicit labels that R4 is shadow/paper and legacy remains live-control.
4. Run targeted server + Flutter tests.

## Task 7 — Full verification

Commands/workflows:
1. Server compile/import checks.
2. Alembic: one head, upgrade, `alembic check`.
3. Full PostgreSQL pytest suite.
4. Tracked-secret scan.
5. Flutter analyze/test and Android release compilation.
6. Repository quality workflow must be SUCCESS at the exact implementation SHA.

## Task 8 — Production deployment and audit

1. Do not deploy while the existing 36-month cold-bootstrap writer is still active; first verify it completed and normal `scheduler-heavy` is restored.
2. Deploy the exact verified implementation SHA using the existing manual server deployment contract.
3. Verify `.signalai-source-sha`, `/health`, migrations and container restart counts.
4. Run/observe aggregate OOS on production immutable snapshots and verify no `TradeIdea` side effects.
5. Verify Paper A/B outcomes feed non-zero promotion evidence scopes when qualifying evidence exists.
6. Confirm live generator remains `legacy_control_v1`, R4 remains shadow/paper-only, and current live funnel remains truthful.

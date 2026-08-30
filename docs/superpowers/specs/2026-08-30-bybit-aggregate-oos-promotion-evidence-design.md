# Bybit aggregate OOS and promotion evidence design

Date: 2026-08-30
Status: approved

## Goal

Make the Bybit strategy competition statistically meaningful and operationally transparent without loosening the production live-control strategy.

## Invariants

1. `legacy_control_v1` remains the production live-control generator. Existing live admission/setup thresholds are not loosened by this change.
2. R4 candidates remain shadow/paper-only. No automatic live promotion is introduced.
3. Existing per-symbol immutable OOS runs remain diagnostic evidence and keep their per-symbol gates.
4. `min_aggregate_trades` is enforced on a true cross-symbol aggregate OOS population, not on each symbol independently and not via an average of per-symbol summary metrics.
5. Aggregate evidence must remain replay-safe and PIT-safe: it may consume only immutable DATA_READY Bybit snapshots and the same strategy evaluator/cost model used by historical entry replay.
6. Aggregate metrics are computed from pooled OOS trade outcomes across the exact snapshot set. Metrics that depend on the distribution/order of trades must not be reconstructed from lossy per-symbol summaries.
7. Paper A/B evidence must have one durable, queryable path from candidate/control decisions through labeled outcomes into promotion evidence. "Immediately labeled" decisions must not disappear from the evidence query.
8. Promotion evidence may mark candidates eligible/ineligible for research governance, but must not switch the live generator automatically.

## Aggregate OOS contract

For each R4 strategy version, resolve the current DATA_READY snapshot for every eligible Bybit perpetual symbol. Evaluate each snapshot with the existing purged walk-forward historical entry replay and pool the resulting OOS trade evidence into one deterministic aggregate run.

The aggregate run is content-addressed by the exact ordered snapshot-set identity plus strategy/config/engine identity. Re-running the same evidence set is idempotent.

At minimum the aggregate report records:

- exact symbols and snapshot IDs;
- per-symbol trade counts and diagnostic status;
- total aggregate trades;
- aggregate expectancy in R;
- aggregate profit factor, including an explicit infinite-PF representation;
- aggregate max drawdown in R over deterministic trade ordering;
- aggregate top-5 contribution;
- aggregate non-annualized trade Sharpe/Sortino where defined;
- aggregate gate criteria and thresholds;
- explicit blockers when there is insufficient DATA_READY coverage.

`min_aggregate_trades` applies only to this aggregate gate. Per-symbol runs remain diagnostic and are not retroactively marked PASS merely because the portfolio aggregate passes.

## Paper A/B evidence contract

Trace and test the full lifecycle:

`shadow candidate/control -> A/B decision -> labeled outcome -> persisted evidence -> promotion scope`.

If current code increments an "immediately labeled" counter without persisting/querying the same outcome representation used by promotion evidence, consolidate the representation or query path so every valid labeled pair is durable and visible exactly once. Preserve idempotency and replay safety.

## Runtime / safety contract

- Production remains PAPER-only.
- Aggregate research and A/B evidence must not publish `TradeIdea` rows.
- No live strategy registry mutation is performed by this change.
- Existing legacy scan path is unchanged except for observability if required.
- Deployment requires full server/migration tests plus the repository quality gate and exact-SHA production provenance verification.

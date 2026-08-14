# Strategy measurement contract

Issue: #40.

## Goal

Make strategy changes measurable before they are promoted. One report must reproduce the same result from a fixed `[from, to)` input period and must never compare champion/candidate on different opportunities.

## Datasets

The contract keeps four datasets separate at every level:

- `BACKTEST` — historical/model outcome;
- `PAPER` — server paper execution;
- `SANDBOX` — broker sandbox execution imported into an outcome record;
- `LIVE` — future real execution; the bucket exists now but remains empty while live trading is disabled.

A record whose dataset cannot be classified is excluded and counted as `unclassified_count`; it is never silently treated as paper or backtest.

## Normalized record

`StrategyMeasurementRecord` contains only measurement inputs:

- `input_id`: immutable opportunity/data identity shared by champion/candidate;
- UTC timestamp;
- dataset and variant/engine version;
- strategy, instrument, venue, regime;
- realized/model outcome in R;
- MFE/MAE in R;
- entry/exit deviation normalized by initial price risk in R;
- confidence;
- operational-failure and reconciliation-mismatch flags;
- `label_usable`.

The server DB adapter uses existing `TradeIdea`, `IdeaOutcome`, `Instrument` and optional `PaperTrade` rows. Non-paper dataset and paired `input_id` provenance are explicit fields in `IdeaOutcome.detail_json` (`measurement_dataset`, `measurement_input_id`, `measurement_regime`). No schema migration is introduced.

## Metrics

For every variant and each dataset, plus groupings by strategy/instrument/venue/regime:

- usable sample size and unusable-label count;
- expectancy / mean outcome R and win rate;
- average MFE/MAE;
- average absolute entry/exit deviation in R;
- maximum cumulative-R drawdown;
- maximum recovery duration in trades;
- confidence calibration by fixed deciles (count, mean confidence, observed win rate, absolute error);
- operational-failure rate;
- reconciliation-mismatch rate;
- `sufficient_sample` against an explicit `min_sample` threshold.

Empty datasets are present with zero/`null` metrics rather than omitted.

## Drawdown/recovery definition

Records are ordered by `(timestamp, dataset, input_id)`. Equity starts at `0R` and adds each usable `outcome_r`. Maximum drawdown is the largest `running_peak - equity`. Recovery duration is the number of subsequent usable records from a peak until equity reaches/exceeds that peak; if recovery has not occurred by period end, duration extends to the final record.

## Confidence calibration

Confidence is clipped only for bucket selection, never rewritten in source data. Buckets are `[0.0,0.1) ... [0.9,1.0]`. Observed win is `outcome_r > 0`. Unusable labels do not enter calibration.

## Champion / candidate

Pairing key is `(dataset, input_id)`. Comparison metrics use the **intersection only**. Champion-only and candidate-only counts are reported separately. Duplicate `(variant, dataset, input_id)` is invalid input and raises instead of choosing an arbitrary record.

## API snapshot

Authenticated read-only endpoint:

`GET /api/v1/measurements/strategies?from_time=...&to_time=...&champion=...&candidate=...&min_sample=...`

The same DB state, period and parameters must serialize to the same report payload except for no generated-now timestamp (the report contains only requested period/provenance).

## Safety

This is measurement only. It changes no strategy thresholds, admissions, risk, broker execution, sandbox mirroring or live-trading mode.
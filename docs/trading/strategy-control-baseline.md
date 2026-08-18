# Strategy control baseline — `legacy_control_v1`

Status: immutable control baseline  
Frozen at: 2026-08-18  
Source commit: `74de570dcaf90900ece5c8e8c6c5f558ca4f49d7`

## Purpose

`legacy_control_v1` is the counterfactual control for future SignalAI strategy
experiments.  It is a measurement identity, not a renamed "latest" strategy.
New candidates must be versioned separately and compared against this exact
snapshot on the same market data and cost model.

This baseline does **not** authorize any change in trading thresholds, risk
caps, execution mode, or LIVE promotion.

## Frozen strategy families

- `TREND_PULLBACK`
- `BREAKOUT_RETEST`
- `WYCKOFF_REVERSAL`

All three families use version `legacy_control_v1` while acting as the legacy
control.  Later versions are separate descriptors; they never mutate this
manifest.

## Exact source identity

| File | Git blob SHA |
|---|---|
| `server/app/strategies/base.py` | `4496b94d7ae806ace1ec7bb298c795bd9a0045c7` |
| `server/app/strategies/trend_pullback.py` | `69be92ff5b79b3ff8b788bff631c9052fd890ba1` |
| `server/app/strategies/breakout_retest.py` | `f357ba5b351b63d7531592964fe9cd44fc120289` |
| `server/app/strategies/wyckoff_reversal.py` | `c28c15ff2a9056a40996eaa80dac5fd1dbcb52ba` |

`strategy_config_hash` for this frozen suite is:

`110d5b5d29560e762f2ee15528bd03ed6ae30b0e6a652b94a40b40eeabd51ada`

The hash is the SHA-256 of canonical JSON containing the source commit, the
four blob SHAs above, and the marker `embedded_strategy_defaults_at_source_sha`.
The marker is intentional: the current strategy parameters are Python defaults
inside these frozen source files, rather than an independently versioned
strategy configuration document.

The risk-policy provenance label is
`legacy_risk_policy@74de570dcaf9`.  SAI-002 persists it on generated ideas;
this PR only freezes the source identity.

## Golden fixtures

`server/tests/strategies/fixtures/control/control_cases.json` is append-only for
this baseline except when a dedicated PR explicitly re-baselines the control.
It currently contains 23 representative scenarios and compact deterministic
bar specifications.  The tests expand those specifications and verify them
against the actual inputs before executing the legacy strategy.

The catalog covers:

- FORTS and crypto identifiers;
- LONG and SHORT control behavior;
- trend pullback, breakout/retest and Wyckoff reversal;
- false breakout;
- extreme volatility and untradeable liquidity;
- insufficient structural history;
- missing confirmation / no-signal branches;
- a qualified pullback that remains a WATCH candidate while the final trigger
  is absent.

A change that alters one of these outcomes must fail CI.  Updating the fixture
to "make CI green" is not an ordinary fix: it requires a separate PR with an
explicit rationale for why the immutable control is being re-baselined.

## Non-destructive rule

The legacy control remains available for replay and later SHADOW comparison
even after another strategy becomes CHAMPION.  UI visibility and broker
execution eligibility are separate concerns and must not delete this history.

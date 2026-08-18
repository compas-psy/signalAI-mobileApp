# Strategy control baseline — `legacy_control_v1`

Status: immutable control baseline  
Frozen at: 2026-08-18  
Source commit: `74de570dcaf90900ece5c8e8c6c5f558ca4f49d7`

## Purpose

`legacy_control_v1` is the counterfactual control for future SignalAI strategy
experiments. It is a **measurement identity only**, not a runtime mode, not a
feature flag, and not a reason to disable the existing strategy path. New
candidates must be versioned separately and compared against this exact
snapshot on the same market data and cost model.

### Runtime invariant

Freezing the baseline must not change runtime execution. `legacy_control_v1`
remains enabled and operational exactly as before for:

- scanning;
- signal generation;
- notification delivery;
- paper lifecycle processing.

Acceptance criterion: after SAI-001, identical runtime inputs must produce the
same production-signal count and the same signal behavior as before SAI-001.
The baseline identity itself must never be consulted as an execution gate.

This baseline does **not** authorize any change in trading thresholds, risk
caps, execution mode, or LIVE promotion.

## Frozen strategy families

- `TREND_PULLBACK`
- `BREAKOUT_RETEST`
- `WYCKOFF_REVERSAL`

All three families use version `legacy_control_v1` while acting as the legacy
control. Later versions are separate descriptors; they never mutate this
manifest or silently replace the legacy runtime path.

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
`legacy_risk_policy@74de570dcaf9`. SAI-002 persists it on generated ideas; the
label is provenance only and does not alter eligibility or execution.

## Golden fixtures

`server/tests/strategies/fixtures/control/control_cases.json` is append-only for
this baseline except when a dedicated PR explicitly re-baselines the control.
It currently contains 23 representative scenarios and compact deterministic
bar specifications. The tests expand those specifications and verify them
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

A change that alters one of these outcomes must fail CI. Updating the fixture
to "make CI green" is not an ordinary fix: it requires a separate PR with an
explicit rationale for why the immutable control is being re-baselined.

The versioning regression suite also recomputes Git blob identities for the
live legacy strategy modules. Future challenger work must be added separately;
it must not mutate these files and thereby change the baseline implicitly.

## Non-destructive rule

The legacy control remains operational and also remains available for replay
and later SHADOW comparison even after another strategy becomes CHAMPION. UI
visibility, measurement role and broker execution eligibility are separate
concerns and must not delete or disable this path implicitly.

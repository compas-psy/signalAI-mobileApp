# Bybit attribution-only acceptance boundary

This slice restores truthful per-instrument strategy-rejection attribution in the Bybit scan funnel.

## Scope

- preserve `instrument_id` alongside each strategy rejection;
- persist deterministic primary rejection code and human detail;
- aggregate multiple strategy rejections without collapsing them to `NO_VALID_SETUP`;
- do not modify strategy thresholds, admission rules, risk, execution, or frozen legacy strategy modules.

## Explicit non-scope

The experimental raw-range change from PR #291 is excluded. The legacy `breakout_retest.py` module is frozen and its golden/versioning tests must remain unchanged.

Historical spread and carry OOS remain separately blocked until their required PIT inputs/outcome contracts are available.

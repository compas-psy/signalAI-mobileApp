# SPREAD Runtime Design

## Goal

Connect persisted official producer-price observations to the existing deterministic `SPREAD` engine without inventing issuer exposures, coefficients, or missing price legs.

## Scope

This slice is an offline database runtime only. It does not add a second SPREAD engine, does not fetch a new source, does not alter trade execution, and does not create an active production issuer basket before a green live-source probe validates the exact official series.

## Architecture

Create `server/app/research/spread_runtime.py` as the adapter between `ResearchObservation` and `engines.spread`.

A `SpreadBasket` is explicit configuration. Every leg names:

- the stable Rosstat `observation_type` produced from OKPD2 + OKEI;
- the expected stored unit (`OKEI:<code>`);
- a fixed coefficient and rationale;
- whether it is a product or input leg.

A basket also carries revenue/cost coverage, contract lag, hedge/vertical-integration flags and an explicit issuer list. Until a real basket is validated from the live official workbook, the production registry is empty. Tests inject fixture baskets directly.

The existing `spread.py` has quarterly semantics: its year-ago comparison is four periods back and its calibration floor is twelve quarters. Rosstat producer prices are monthly. Therefore the runtime must aggregate monthly observations into calendar-quarter averages before constructing `spread.Period` objects. A quarter is usable only if every configured leg has exactly one usable numeric observation in each of all three constituent months.

## Data flow

1. Query only `rosstat` observations for the basket's exact machine series.
2. Enforce `tradable_at <= as_of` before any numeric value is used.
3. Reject observations whose unit differs from the configured unit.
4. Group usable rows by calendar quarter and leg.
5. Require January-February-March, April-May-June, July-August-September or October-November-December to be complete for every configured leg. Missing or duplicate monthly points make the whole quarter unusable.
6. Compute each leg's arithmetic mean across its three monthly producer prices. Build one `spread.Period` per complete quarter from those averages.
7. If no basket, a series is missing, a unit mismatches, or complete quarterly history is insufficient, return an explicit no-signal reason and do not call the common pipeline.
8. Otherwise call the existing `spread.evaluate()` with the basket's coverage/lag/integration parameters.
9. Emit `SignalInput` only for issuers explicitly named in the validated basket. The common fusion/pipeline remains responsible for hypothesis persistence; D1 market context is only an overlay in resolution, never a substitute for a fundamental leg.

## Safety rules

- No fuzzy series matching by label; only stable machine observation keys.
- No forward filling and no zero substitution for missing legs or months.
- No use before persisted `tradable_at`.
- No partial quarter and no partial optimistic spread.
- No conversion of monthly rows directly into engine periods.
- No default issuer exposure before live validation.
- No trading/execution behavior changes.

## Testing

Regression tests must prove:

- observations after `as_of` are invisible;
- a quarter missing one month or one leg is excluded;
- three monthly values become one arithmetic quarterly average;
- four engine periods represent one year, so twelve complete quarters are required for calibrated runtime history;
- unit mismatch fails closed;
- an empty production basket registry cannot emit an issuer signal;
- a fixture basket with an explicit issuer can reach `SignalInput`/pipeline only when all required evidence is complete.

## Follow-up gate

After this slice is green and merged, run a live Rosstat source probe, inspect actual current OKPD2/OKEI series, then add the first narrow production sector basket with documented coefficient rationale and explicit issuer exposure in a separate reviewed change.
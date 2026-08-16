# Portfolio Issuer Concentration Design

## Context

Issue #86 Stage 3 has already made portfolio screening and optimizer history strict as-of, added stale-data rejection, and added mature-negative-research guardrails. The remaining concentration gap is issuer aggregation: the system currently has security identity but no reliable legal-issuer identity, and prior work explicitly prohibited guessing issuer identity from ticker/name.

## Decision

Use only an explicit issuer identifier supplied by MOEX metadata. Persist that identifier in the existing `Instrument.metadata_json` rather than adding a schema column until the source contract is proven stable across the investment boards. Portfolio construction groups securities by this identifier and applies the existing single-name concentration ceiling to the aggregate issuer exposure.

No fuzzy matching, ticker-prefix matching, title parsing, manually maintained issuer map, or LLM classification is allowed.

## Data flow

1. `market/moex.py` requests and exposes the exchange issuer field when available.
2. `market/investments.py` persists it as `metadata_json.issuer_id`.
3. `portfolio/build.py` reads explicit issuer IDs while building optimisation groups.
4. Securities sharing an issuer ID are constrained as one concentration group.
5. Missing issuer IDs are not merged with any other security and are never guessed.

## Failure behavior

A missing issuer identifier must remain visible as missing metadata; it must not cause an entire investment-universe sync to fail. The portfolio continues to enforce the existing per-security ceiling for such instruments, while issuer aggregation is applied only where identity is source-backed. This is safer than a false issuer mapping and preserves availability while the source coverage improves.

## Compatibility

No API response contract needs to change for this Stage 3 slice. Existing portfolio profiles, class/crypto caps, research evidence, walk-forward admission, rebalance advisory behavior, and execution boundaries remain unchanged.

## Verification

Tests must prove: explicit same-issuer securities aggregate under one ceiling; different issuers do not; missing issuer identity is not guessed; universe sync preserves source issuer identity; existing portfolio tests remain green. Exact-head repository Quality Gate is required before merge.

# Russian Equities Early Radar — full-universe owner UX

Date: 2026-08-17
Status: approved design
Target: SIGNAL AI thin client + server API

## Goal

Replace the current narrow owner-facing Russian-equities presentation with one transparent full-universe early radar. The user should see every ranked Russian equity the server evaluated, with the strongest early candidates first and weak/late names still visible with explicit reasons.

The radar is investment/research advisory only. It must not create short-term orders, entries, stops, quantities, approvals, or live execution actions.

## Product contract

### Full universe

- Show the full ranked equity universe returned by the production server.
- Do not impose an artificial Top-N cutoff in the primary view.
- Default sort is server rank ascending.
- Weak/rejected/late names remain visible rather than disappearing.
- The client must not recompute ranking scores or eligibility.

### Compact row

Each collapsed row shows at minimum:

- rank;
- ticker;
- company title;
- overall score;
- early score;
- early state.

Canonical early states come from the server snapshot and include:

- `ранняя подготовка`;
- `формируется`;
- `наблюдать`;
- `раннего преимущества нет`;
- `поздно / не догонять`;
- data-insufficient state when history is insufficient.

### Expanded details

An expanded row shows server-provided evidence grouped for owner readability:

1. **Почему сейчас**
   - volatility compression;
   - short-vs-medium acceleration;
   - proximity to 63-session high/breakout;
   - turnover expansion;
   - accumulation share;
   - anti-chase warning when applicable.

2. **Качество идеи**
   - fundamental score and facts;
   - D1 technical score/state and facts;
   - catalyst/research hypothesis when present.

3. **Подтверждение**
   - server-provided confirmation condition for the early hypothesis.

4. **Инвалидация**
   - server-provided invalidation condition.

5. **Динамика**
   - rank change when available;
   - 5d and 20d returns;
   - breakout distance;
   - turnover ratio;
   - compression ratio;
   - accumulation score;
   - chase penalty.

6. **Предупреждения**
   - explicit anti-chase/late-extension status;
   - fundamental/research/data-quality warnings already present in the snapshot.

### Filters

The default view is **Все** and contains the complete universe. Optional owner filters may reduce the visible list without changing server rank or data:

- Все;
- Ранние;
- Наблюдать;
- Поздно.

Filtering is client-side presentation only and must never change the server snapshot or ranking.

## Architecture

### Server

Reuse the existing `equity_rank_v2_early` computation as the single ranking source of truth. The implementation should first inspect the current `/api/v1/research/equity-ranking` and `/api/v1/research/investment-signals` contracts.

Preferred implementation:

- expose the already-persisted early-ranking fields through the existing equity-ranking endpoint if they are not already serialized;
- avoid adding a second ranking engine;
- avoid duplicating calculations inside an API adapter;
- preserve strict as-of/no-forward semantics from `equity_rank_v2_early`;
- preserve weak/rejected names in the payload.

The API payload must include enough data for the client to render the compact and expanded states without local financial calculations.

Required per-item fields, when measurable:

- `rank`;
- `rank_change`;
- `instrument_id`;
- `symbol`;
- `title`;
- `score`;
- `tier`;
- `eligible`;
- `fundamental_score`;
- `technical_score`;
- `technical_state`;
- `early_score`;
- `early_state`;
- `early_eligible`;
- `chase_penalty`;
- `why_now`;
- `confirmation`;
- `invalidation`;
- `return_5d`;
- `return_20d`;
- `return_3m`;
- `return_6m`;
- `breakout_distance`;
- `turnover_ratio`;
- `accumulation_score`;
- `compression_ratio`;
- `fundamental_facts`;
- `technical_facts`;
- `warnings`;
- optional research hypothesis/catalyst object;
- snapshot freshness/as-of metadata.

If a metric is not measurable, return `null` rather than inventing or defaulting a financial value.

### Flutter thin client

Use the existing thin-client research data source and domain models. Extend them rather than creating a parallel local ranking stack.

Primary owner screen remains the existing investment-signals/research area. Do not add a new navigation destination solely for this milestone.

The client responsibilities are:

- fetch the production snapshot;
- preserve server rank;
- render the complete universe;
- expand/collapse rows;
- apply presentation filters;
- format measured values;
- surface partial/unavailable server state without fabricating cached rankings.

The client must not calculate early score, anti-chase, rank, confirmation, invalidation, or investment eligibility.

## Ordering and late names

The default order is the server rank, not a client-side heuristic. Late-extension/chased names should naturally rank lower because the server methodology applies anti-chase penalties. The UI additionally makes the late state visually explicit.

Do not silently hide a high overall-score name merely because it is late. It must remain visible with the late/anti-chase warning so the user can understand why it is not an early opportunity.

## Failure and freshness behavior

- If the endpoint is unavailable, show an explicit server-unavailable state and retry action.
- If the server returns an empty universe, explain counts/reason instead of showing a blank screen.
- If some metrics are null, render `—` or omit that metric without substituting zero.
- Show server `data_as_of`/snapshot freshness prominently enough to distinguish fresh market data from stale state.
- Do not fall back to local ranking calculations.

## Non-goals

This milestone does not:

- create trade entries, stops, targets, quantities or approvals for equities;
- execute real or PAPER equity trades;
- change the short-term crypto/FORTS scanner;
- weaken ranking or anti-chase thresholds;
- change portfolio allocation/rebalance policy;
- add external broker integration;
- promise predictive certainty or returns.

## Testable acceptance criteria

### Server

1. The equity-ranking response exposes the full ranked universe, not only the current short action shortlist.
2. Every ranked item preserves the server rank and includes early-state fields where available.
3. A synthetic compressed/pre-breakout candidate can expose a stronger early state than a vertically extended post-spike candidate.
4. A chased/late candidate remains present but exposes a positive chase penalty and late state.
5. Missing measurements serialize as null, not fabricated zero values.
6. Strict as-of/no-forward ranking tests remain green.

### Flutter

1. A fixture with more than 10 equities renders all items in the default `Все` view.
2. Rows preserve server order/rank.
3. Expanding a row exposes why-now, confirmation, invalidation and anti-chase data when present.
4. Client filters only affect visibility; clearing the filter restores the entire fixture.
5. Null metrics render safely without misleading numeric zeros.
6. Unavailable/empty states are explicit and recoverable.

### Release

- server suite green;
- Flutter analyze/tests green;
- secret scan green;
- PR diff limited to the equities-radar milestone plus its design/plan/tests;
- merge to `claude/release-y40hk5` only after green gate;
- meaningful cumulative production deploy and signed sideload APK from the exact merged SHA.

## Success definition

On the Samsung thin client, the owner can open the Russian-equities research/signals area and inspect the entire server-ranked universe. The most promising pre-move candidates are visible first, each can explain why it surfaced now, what confirms it, what invalidates it, and whether anti-chase says the move is already too late. No equity is hidden merely because it is weak, rejected, or late.
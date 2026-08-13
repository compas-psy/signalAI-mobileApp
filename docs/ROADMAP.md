# SignalAI Roadmap

Актуально на 13 августа 2026 года.

## Целевая функция

SignalAI должен максимизировать долгосрочный риск-скорректированный рост Equity на Bybit и в контуре Т‑Инвестиций/российского рынка после комиссий, проскальзывания и налогов при контролируемой просадке, risk of ruin, ликвидности и информационной безопасности.

Постоянный рост Equity не гарантируется. Любое изменение trading logic должно иметь проверяемый edge и не оцениваться только по in-sample backtest.

## Текущее production-состояние

Последний подтверждённый production source SHA перед foundation-настройкой:
`0484a46efe92877bbb8e16abf68bb12b16af0c42`.

Уже есть thin-client/server source of truth, owner approve/reject, server-side paper lifecycle, FORTS/crypto live progress, T‑Invest Sandbox mirror, Risk & Exit Engine v2, portfolio signals и Telegram notifications с deep links.

## NOW — P−1 · Project Operating System

Цель: сделать развитие SignalAI управляемым и воспроизводимым до следующей продуктовой итерации.

DoD:

- [ ] `AGENTS.md` фиксирует product objective, money/security invariants, Context7/skills policy и cumulative delivery;
- [ ] ADR-процесс и первый ADR promotion gates добавлены;
- [ ] этот roadmap становится текущей точкой правды, а `HANDOFF.md` остаётся историей;
- [ ] `Cumulative production release` запускается только вручную, а не на каждый push;
- [ ] один explicit release запускает канонические VPS/APK delivery не более одного раза каждый;
- [ ] stale issues #4/#6 актуализированы;
- [ ] отдельная CI-задача фиксирует следующий шаг: один общий QA и selective runtime delivery без повторных проверок;
- [ ] foundation PR проходит Quality Gate и не запускает production release автоматически.

## NEXT — P0 · Production reliability & observability

Канонический цикл:

`signal → notification → idea detail → owner decision → execution/paper/sandbox → management → exit → journal → metrics`.

Нужны crash/error telemetry с redaction, data-quality telemetry, execution/reconciliation telemetry, runtime health для scheduler/outbox/adapters и Samsung smoke/soak tests. Каждый пользовательский дефект должен превращаться в reproducible regression test.

Gate выхода: нет известных blocker/crash в основном end-to-end сценарии, а сбой диагностируется без временной forensic-ветки.

## P0.5 · Trading performance measurement

Единый performance layer должен считать минимум:

- net realized PnL и realized R;
- expectancy после fees/slippage;
- sample-aware win rate;
- drawdown/recovery;
- MFE/MAE и exit efficiency;
- slippage и execution error rate;
- score/probability calibration;
- contribution по strategy / asset / venue / market regime;
- concentration и correlation clusters.

Эти метрики должны управлять champion/challenger и promotion gates, а не быть только dashboard.

## P1 · Alpha / Early Equity Radar

Ближайшие независимые фундаментальные каналы:

1. `HIRING` — структурные изменения вакансий через официальный источник «Работа России»;
2. `SPREAD` — отраслевой margin spread на официальных рядах с lag/coverage controls.

Дальше research engines добавляются только при измеримом incremental predictive value вне выборки.

Принцип:

`raw source → Observation(tradable_at/provenance) → engine → fusion → hypothesis → D1 technical overlay → owner-facing action`.

LLM не является источником факта и не создаёт сигнал без измеряемых данных.

## P2 · Portfolio allocator

Превратить `ACCUMULATE / EARLY / WATCH` в allocation decisions для трёх режимов: консервативный, оптимальный, агрессивный. Учитывать expected return, downside/risk contribution, correlation, liquidity, concentration, текущий портфель и rebalance cost.

## P3 · Controlled Live

Переход только по ADR `0001-live-promotion-gates.md`:

`Research → Backtest → OOS/Walk-forward → Shadow → Paper → Broker Sandbox/Testnet → Canary Live → Scaled Live`.

Canary Live требует operational correctness, подтверждённый net edge, acceptable drawdown/tail risk, security gate, kill switch и явное решение владельца о размере canary capital. Деградация edge/execution возвращает стратегию в paper/sandbox.

## Источник истины

1. `AGENTS.md` — правила работы;
2. этот `ROADMAP.md` — текущий приоритет;
3. accepted ADR — долгосрочные решения;
4. open issues/PR — исполнимые задачи;
5. default-branch code и актуальные docs.

`HANDOFF.md` хранит историю ранних итераций и не определяет текущие следующие шаги.

## Release policy

Merge в default branch не является production release. Нормальная поставка: завершённый logical batch → explicit `Cumulative production release` → exact accepted `source_ref` → канонические delivery workflows. Прямые deploy/sideload workflows — исключение для hotfix/rollback/ops.

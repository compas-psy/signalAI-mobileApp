# SignalAI Roadmap

Актуально на 13 августа 2026 года.

## Целевая функция

SignalAI должен максимизировать долгосрочный риск-скорректированный рост Equity на Bybit и в контуре Т‑Инвестиций/российского рынка после комиссий, проскальзывания и налогов при контролируемой просадке, risk of ruin, ликвидности и информационной безопасности.

Постоянный рост Equity не гарантируется. Изменения trading logic не оцениваются только по in-sample backtest.

## Текущее production-состояние

Последний подтверждённый production source SHA перед foundation-настройкой:
`0484a46efe92877bbb8e16abf68bb12b16af0c42`.

Уже есть thin-client/server source of truth, owner approve/reject, server-side paper lifecycle, FORTS/crypto live progress, T‑Invest Sandbox mirror, Risk & Exit Engine v2, portfolio signals и Telegram notifications с deep links.

## NOW — P−1 · Project Operating System

Foundation реализуется PR #38.

- [x] `AGENTS.md` фиксирует product objective, engineering/security invariants, Context7/skills policy и cumulative delivery.
- [x] Добавлены ADR-процесс, шаблон и ADR-0001 promotion gates.
- [x] `ROADMAP.md` — текущая точка правды; `HANDOFF.md` остаётся историей.
- [x] `Cumulative production release` больше не запускается на каждый push; trigger manual-only.
- [x] Один explicit release dispatch запускает не более одного canonical VPS и одного canonical APK workflow.
- [x] Issue #6 обновлён под текущий device acceptance.
- [x] Issue #4 очищен от зависимости на закрытый PR #3.
- [x] `SECRETS.md` приведён к фактическому device-only T‑Invest Sandbox boundary.
- [ ] Runtime enforcement immutable current-default SHA — issue #41.
- [ ] PR #38 должен быть слит только после exact-head green Quality Gate; merge сам по себе production release не запускает.

## NEXT — P0 · Production reliability & observability

Backlog: issue #6 и issue #39.

Канонический цикл:

`signal → notification → idea detail → owner decision → execution/paper/sandbox → management → exit → journal → metrics`.

Нужны crash/error telemetry с redaction, data-quality telemetry, reconciliation telemetry, runtime health для scheduler/outbox/adapters и Samsung smoke/soak tests. Каждый пользовательский дефект превращается в reproducible regression test.

Gate выхода: нет известных blocker/crash в основном end-to-end сценарии, а сбой диагностируется без временной forensic-ветки.

## P0.5 · Strategy measurement

Backlog: issue #40.

Measurement layer разделяет backtest / paper / sandbox / live и считает outcome по strategy / instrument / venue / regime, MFE/MAE, drawdown/recovery, execution deviation, confidence calibration и operational failure rates. Малые выборки маркируются как недостаточные.

Эти измерения используются для champion/challenger и promotion gates, а не только для dashboard.

## P1 · Alpha / Early Equity Radar

Backlog: issue #4.

Ближайшие независимые фундаментальные каналы:

1. `HIRING` — структурные изменения вакансий через официальный источник;
2. `SPREAD` — отраслевой margin spread на официальных рядах с lag/coverage controls.

Принцип:

`raw source → Observation(tradable_at/provenance) → engine → fusion → hypothesis → D1 technical overlay → owner-facing action`.

Новые research engines добавляются только при измеримом incremental predictive value вне выборки. LLM не является источником факта.

## P2 · Portfolio allocator

Превратить `ACCUMULATE / EARLY / WATCH` в allocation decisions для трёх режимов: консервативный, оптимальный, агрессивный. Учитывать expected return, downside/risk contribution, correlation, liquidity, concentration, текущий портфель и rebalance cost.

## P3 · Controlled Live

Переход только по ADR `0001-live-promotion-gates.md`:

`Research → Backtest → OOS/Walk-forward → Shadow → Paper → Broker Sandbox/Testnet → Canary Live → Scaled Live`.

Canary Live требует operational correctness, подтверждённый edge, acceptable drawdown/tail risk, security gate, kill switch и явное решение владельца. Деградация возвращает стратегию в paper/sandbox.

## Источник истины

1. `AGENTS.md` — правила работы;
2. этот `ROADMAP.md` — текущий приоритет;
3. accepted ADR — долгосрочные решения;
4. open issues/PR — исполнимые задачи;
5. default-branch code и актуальные docs.

`HANDOFF.md` хранит историю ранних итераций и не определяет следующие шаги.

## Release policy

Merge в default branch не является production release. Нормальная поставка: завершённый logical batch → explicit `Cumulative production release` → exact accepted immutable `source_ref` → канонические delivery workflows. Прямые deploy/sideload workflows — исключение для hotfix/rollback/ops.

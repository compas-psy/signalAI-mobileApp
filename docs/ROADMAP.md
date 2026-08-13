# SignalAI Roadmap

Актуально на 13 августа 2026 года.

## Целевая функция

SignalAI должен максимизировать долгосрочный риск-скорректированный рост Equity на Bybit и в контуре Т‑Инвестиций/российского рынка после комиссий, проскальзывания и налогов при контролируемой просадке, risk of ruin, ликвидности и информационной безопасности.

Постоянный рост Equity не гарантируется. Изменения trading logic не оцениваются только по in-sample backtest.

## Текущее состояние

Последний подтверждённый **production source SHA**:
`0484a46efe92877bbb8e16abf68bb12b16af0c42`.

Текущий **default-branch SHA** после foundation/security/research merges:
`755a72ed35b19c358a9c281e4562b498ff1d97a6`.

Merge в default не является production release, поэтому эти два SHA сейчас намеренно различаются.

Уже есть thin-client/server source of truth, owner approve/reject, server-side paper lifecycle, FORTS/crypto live progress, T‑Invest Sandbox mirror, Risk & Exit Engine v2, portfolio signals и Telegram notifications с deep links.

## DONE · Project Operating System foundation

Foundation PR #38 слит после exact-head green Quality Gate. Security follow-up PR #44 также слит.

- [x] `AGENTS.md` фиксирует product objective, engineering/security invariants, Context7/skills policy и cumulative delivery.
- [x] Добавлены ADR-процесс, шаблон и ADR-0001 promotion gates.
- [x] `ROADMAP.md` — текущая точка правды; `HANDOFF.md` остаётся историей.
- [x] `Cumulative production release` manual-only; merge сам production delivery не запускает.
- [x] Один explicit release dispatch запускает не более одного canonical VPS и одного canonical APK workflow.
- [x] Issue #6 обновлён под текущий device acceptance.
- [x] Issue #4 больше не зависит от закрытого PR #3.
- [x] `SECRETS.md` соответствует device-only T‑Invest Sandbox boundary.
- [x] Android native boundary не экспортирует `*.secret` через generic `vaultGet` (PR #44).
- [ ] Runtime enforcement immutable current-default SHA — issue #41.
- [ ] Pin VPS SSH host authenticity вместо live TOFU — issue #43.

## NOW — P0 · Execution reliability & observability

Backlog: issue #45, issue #39 и issue #6.

Канонический цикл:

`signal → notification → idea detail → owner decision → execution/paper/sandbox → management → exit → journal → metrics`.

Главный execution blocker — #45: decision idempotency уже есть, но T‑Invest Sandbox mirror должен стать отдельной durable/replay-safe delivery state machine со стабильным provider order id и repair после restart/ambiguous response.

По observability серверный `/health` уже показывает version/config hash, execution mode, paper-only, kill switch и DB status. Следующий минимальный mobile slice — bounded redacted local crash/error history поверх существующего `LocalStore`, затем wiring глобальных Flutter/async error boundaries. Нужны также data-quality/reconciliation counters и Samsung smoke/soak из #6.

Gate выхода: нет известных blocker/crash в основном end-to-end сценарии, а сбой диагностируется и после перезапуска приложения.

## P0.5 · Strategy measurement

Backlog: issue #40.

Measurement layer разделяет backtest / paper / sandbox / live и считает outcome по strategy / instrument / venue / regime, MFE/MAE, drawdown/recovery, execution deviation, confidence calibration и operational failure rates. Малые выборки маркируются как недостаточные.

Эти измерения используются для champion/challenger и promotion gates, а не только для dashboard.

## P1 · Alpha / Early Equity Radar

Backlog: issue #4.

### HIRING

HIRING уже не нужно строить с нуля: в default есть официальный `trudvsem` adapter, `hiring.py`, `hiring_runtime.py`, scheduler wiring, tests/legal checks, `modifiedFrom`, pagination и выход в common research pipeline.

Оставшиеся correctness gaps:

1. employer → issuer fallback должен быть fail-closed: strong INN выигрывает, но substring brand match без strong identifier недопустим;
2. runtime должен применять/persist first-seen availability / `tradable_at`, а не только publication/modification timestamp;
3. проверить durable first-seen и republishing dedupe на live coverage.

### SPREAD

Deterministic `engines/spread.py` уже реализован: period averages, product/input coefficients, revenue/cost coverage, contract lag, hedging, vertical integration, outage/capturability. Не хватает production runtime/ingestion.

PR #48 слит: появился fail-closed discovery актуального официального Rosstat XLSX «Средние цены производителей промышленных товаров (услуг) с 1998 г.» по стабильной странице-каталогу, только HTTPS + Rosstat host + exact dataset title. Неполный XLSX-reader эксперимент #49 закрыт и в default не попадал.

Следующий SPREAD chain:

`official Rosstat/EIA/CBR series → fixed sector basket → period-average Period[] → spread.evaluate() → SignalInput → fusion/pipeline`.

Workbook schema не угадывать: сначала получить и зафиксировать реальную структуру официального vintage-файла/fixture, затем parser. Недостаточные/пропавшие серии должны давать explicit no-signal reason.

Общий принцип research:

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

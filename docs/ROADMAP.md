# SignalAI Roadmap

Актуально на 13 августа 2026 года.

## Целевая функция

SignalAI должен максимизировать долгосрочный риск-скорректированный рост Equity на Bybit и в контуре Т‑Инвестиций/российского рынка после комиссий, проскальзывания и налогов при контролируемой просадке, risk of ruin, ликвидности и информационной безопасности.

Постоянный рост Equity не гарантируется. Изменения trading logic не оцениваются только по in-sample backtest.

## Где мы находимся

**Production runtime** пока остаётся на source SHA:
`0484a46efe92877bbb8e16abf68bb12b16af0c42`.

**Текущий default-branch HEAD** после foundation и Android vault hardening:
`c7c0d9a011734c0b793b7709b2d7478f5721df3c`.

Эти SHA различаются намеренно: merge в default branch больше не является production release. Foundation/docs/security changes не требовали немедленного APK/VPS deploy.

Уже есть thin-client/server source of truth, owner approve/reject, server-side paper lifecycle, FORTS/crypto live progress, T‑Invest Sandbox mirror, Risk & Exit Engine v2, Portfolio Signals и Telegram notifications с deep links.

## P−1 · Project Operating System — практически завершён

PR #38 слит после exact-head Quality Gate #168. PR #44 с Android native vault boundary слит после exact-head Quality Gate #170.

- [x] `AGENTS.md` фиксирует product objective, engineering/security invariants, Context7/skills policy и cumulative delivery.
- [x] Добавлены ADR-процесс, шаблон и ADR-0001 promotion gates.
- [x] `ROADMAP.md` — текущая точка правды; `HANDOFF.md` считается историческим материалом.
- [x] `Cumulative production release` больше не запускается на каждый push; trigger manual-only.
- [x] Один explicit release dispatch запускает не более одного canonical VPS и одного canonical APK workflow.
- [x] Issue #6 обновлён под текущий device acceptance.
- [x] Issue #4 очищен от зависимости на закрытый PR #3.
- [x] `SECRETS.md` приведён к фактическому device-only T‑Invest Sandbox boundary.
- [x] HMAC `*.secret` больше нельзя экспортировать из Android native vault через generic `vaultGet`; CI проверяет этот инвариант.
- [ ] Runtime enforcement immutable current-default release SHA — issue #41.

## NOW — P0 · Production reliability, execution correctness, observability

Канонический цикл:

`signal → notification → idea detail → owner decision → paper/sandbox delivery → management → exit → journal → metrics`.

### P0 backlog

- **#6 Device acceptance & runtime stability** — реальный Samsung smoke/soak test.
- **#39 Runtime observability and end-to-end health** — crash/error/data-quality/reconciliation diagnostics без утечки секретов.
- **#45 Replay-safe and durable sandbox mirror** — server decision и provider delivery должны быть двумя отдельными идемпотентными состояниями; retry/restart обязан сходиться к одной доставке.
- **#43 VPS SSH host identity** — production SSH delivery не должен строить доверие только на live `ssh-keyscan`.
- **#41 Immutable cumulative release source** — QA/VPS/APK должны использовать один уже зафиксированный SHA.

### Главный новый blocker перед controlled live

В `SandboxMirroringEngineClient` повторный owner decision с `idempotentReplay=true` сейчас прекращает broker mirror. Если первый server response потерян или процесс умер между decision и provider delivery, server decision уже существует, а sandbox delivery может так и не быть восстановлена.

Дополнительно T‑Invest adapter сейчас генерирует provider `orderId` от времени. Поэтому слепой retry может стать новой заявкой. Для #45 нужен стабильный provider identity от immutable `TradeIdea.id` + durable delivery state + reconciliation перед повторной отправкой.

Gate выхода P0: нет известных blocker/crash в основном end-to-end сценарии; ambiguous retry/restart не создаёт дубль и не теряет delivery; сбой диагностируется без временной forensic-ветки.

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

# ADR-0001: Promotion gates от исследования до live trading

- Status: accepted
- Date: 2026-08-13
- Owners: Product owner + Integrator/CTO + Quant
- Supersedes: —

## Контекст

SignalAI уже умеет генерировать торговые идеи, сопровождать server-side paper lifecycle и зеркалировать подтверждённые FORTS-планы в T‑Invest Sandbox. Цель проекта — рост risk-adjusted Equity, но прямой переход от успешного backtest или sandbox к live создаёт риск переобучения, execution defects и потери капитала.

Нужна единая последовательность продвижения стратегий, которая отделяет проверку edge от технической готовности исполнения.

## Decision drivers

- Положительное математическое ожидание должно подтверждаться вне обучающей выборки и на forward-контуре.
- Комиссии, проскальзывание, ликвидность и реальные ограничения брокера учитываются до live.
- Нельзя повышать капитал, если operational correctness не подтверждена.
- Live credentials и капитал не должны компенсировать недостаток данных или тестирования.
- Информационная безопасность и hard risk caps являются ограничениями, а не оптимизируемыми параметрами.

## Рассмотренные варианты

### A. Backtest → Live

Быстро, но неприемлемо: высокий риск leakage/overfit и не проверяет реальное исполнение.

### B. Backtest → Paper → Live

Лучше, но paper не проверяет broker API, idempotency, protection и фактические ограничения исполнения.

### C. Полный promotion pipeline

`Research → Backtest → OOS/Walk-forward → Shadow → Paper → Broker Sandbox/Testnet → Canary Live → Scaled Live`.

Выбран вариант C.

## Решение

Каждая новая стратегия или существенное изменение admission/risk/exit проходит этапы последовательно. Пропуск этапа возможен только для изменения, которое доказуемо не влияет на trading behavior, и должен быть явно зафиксирован в PR.

### Research

Формулируется гипотеза, источник edge, допустимые данные и `tradable_at`. Запрещён look-ahead/data leakage.

### Backtest

Проверяются базовая логика, costs/slippage assumptions и failure cases. In-sample результат не является основанием для продвижения.

### OOS / Walk-forward

Оцениваются net expectancy, drawdown, sample adequacy, regime stability, concentration и calibration. Параметры не подгоняются по test slice.

### Shadow

Стратегия работает на live market data без создания paper/broker orders. Проверяются timeliness, data quality и расхождение с historical assumptions.

### Paper

Server-side lifecycle проверяет entry/SL/TP/runner/exit, risk budget, журнал и восстановление после restart.

### Broker Sandbox / Testnet

Проверяются broker-specific order semantics, idempotency, protection, rounding/lot size, cancellation, reconciliation и network failures. Sandbox credentials отделены от live credentials.

### Canary Live

Включается только после явного решения владельца. Используется малый заранее утверждённый капитал и неизменяемые hard caps. Kill switch и fail-closed проверены до первой сделки.

### Scaled Live

Капитал увеличивается ступенчато только при подтверждении фактического net edge, acceptable drawdown и operational error rate. Деградация переводит стратегию назад в paper/sandbox до разбора.

## Последствия

- До live путь становится длиннее, зато технический успех не смешивается с financial edge.
- Нужно хранить метрики по stages и versions strategy/risk policy.
- Champion/challenger optimization может менять только будущие policy snapshots и не переписывает открытые сделки.
- Security или execution regression блокирует promotion независимо от historical return.

## Acceptance criteria

- [ ] Performance layer хранит stage, strategy/policy version и достаточные metrics для promotion decision.
- [ ] Ни один live adapter не активируется без явного owner-controlled gate.
- [ ] Broker operations idempotent и имеют reconciliation/fail-closed tests.
- [ ] Canary limits и kill switch проверяются до первого live order.
- [ ] Promotion/demotion decision оставляет append-only audit trail.

## Условия пересмотра

ADR пересматривается при появлении нового broker execution model, существенном изменении регуляторных/биржевых ограничений или накоплении достаточной production-статистики, показывающей необходимость изменить этапы.

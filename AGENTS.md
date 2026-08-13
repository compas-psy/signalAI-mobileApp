# SIGNAL AI engineering rules

These instructions apply to the entire repository.

## Communication and objective

- С владельцем проекта общаться на русском, если он явно не просит иначе.
- Цель SignalAI: максимизировать долгосрочный риск-скорректированный рост Equity после комиссий, проскальзывания и налогов при контролируемой просадке, risk of ruin, ликвидности и информационной безопасности.
- Не обещать постоянный рост Equity. Trading changes оцениваются по проверяемому edge, а не по одному backtest или отдельным удачным сделкам.

## Engineering discipline

- Перед реализацией явно назвать существенные assumptions и trade-offs.
- Выбирать минимальное достаточное решение; не добавлять speculative abstractions.
- Менять только файлы, связанные с текущим DoD; не рефакторить соседний код «заодно».
- Любую задачу переводить в проверяемую цепочку: change → test/check → acceptance result.

## Trading and money

- Signal admission, risk sizing, execution и exit management — разные ответственности; один слой не должен скрыто менять другой.
- Неподтверждённая владельцем идея не становится live-сделкой.
- Текущий production-контур остаётся paper/sandbox до отдельного owner decision и прохождения promotion gates из `docs/adr/0001-live-promotion-gates.md`.
- Money/execution operations должны быть idempotent, иметь fail-closed behavior и reconciliation.
- Hard risk limits и kill switch нельзя обходить ради доходности.
- Существенное изменение trading logic требует OOS/walk-forward проверки после realistic costs и проверки на data leakage.

## Security

- Соблюдать least privilege; credentials и чувствительные данные не должны попадать в git, issues, build constants, логи или telemetry.
- Текущая архитектура credentials определяется кодом, `docs/SECRETS.md` и accepted ADR; security-sensitive изменение требует отдельного review/scan.
- Security fix не должен незаметно менять strategy admission, thresholds или risk profile.

## External documentation and skills

- Если изменение зависит от внешней библиотеки, SDK или API, сначала проверять актуальную документацию через Context7, если он доступен.
- Для broker/exchange API, GitHub Actions, auth и денежных операций дополнительно сверяться с официальной документацией провайдера.
- Если задача специализированная и текущих возможностей не хватает, сначала искать готовый skill/plugin; не создавать новый meta-skill без повторяющейся необходимости.
- Superpowers использовать точечно для debugging, test-first/TDD, verification и review. Codex Security — для security-sensitive work. UX/UI skills — только для UX/UI задач.

## ADR and source of truth

Существенные решения по архитектуре, деньгам и безопасности фиксировать в `docs/adr/`.

Порядок источников истины:

1. `AGENTS.md`;
2. `docs/ROADMAP.md`;
3. accepted ADR;
4. current open issues/PR и default-branch code;
5. актуальные development/API/build/security docs.

`HANDOFF.md` — исторический документ ранних итераций, а не текущий roadmap.

## GitHub and cumulative delivery

- Один logical vertical slice → одна короткоживущая branch → один связный PR.
- PR/CI может проверять промежуточные коммиты; production delivery не запускается на каждый push.
- Merge в default branch сам по себе не является release.
- Обычная production-поставка запускается явно через `Cumulative production release` для принятого `source_ref` после завершения логического batch.
- Текущий orchestrator запускает канонические VPS/APK delivery workflows максимум по одному разу на release. Дальнейшая дедупликация повторных QA и selective delivery — отдельная CI-задача.
- Не запускать build/deploy, если его результат заведомо сразу будет заменён следующим изменением. Исключения: hotfix, rollback и явно обозначенные ops/forensic действия.

## Completion

Перед заявлением «готово» пройти релевантные tests/checks и project Quality Gate. Для money/risk/execution обязательны regression tests на idempotency/failure boundaries; для trading logic — OOS evidence; для production delivery — подтверждение exact source SHA/provenance.

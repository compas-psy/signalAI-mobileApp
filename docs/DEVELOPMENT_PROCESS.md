# Процесс разработки SignalAI

SignalAI — персональный продукт: один владелец, один server source of truth и thin Android client. Цель и приоритеты определены в `docs/ROADMAP.md`, обязательные правила — в `AGENTS.md`, существенные решения — в `docs/adr/`.

## Роли и границы

| Роль | Отвечает | Не меняет одновременно |
|---|---|---|
| Product owner / CPO | сценарий, риск, acceptance, решение о live | код и thresholds без зафиксированного решения |
| Quant / research | trading rules, features, backtest/OOS, качество идеи | UI, execution и CI |
| Backend | API, БД, migrations, calculation, lifecycle, audit | мобильную копию server business logic |
| Mobile | presentation, owner decision, device Keystore, push | server risk, sizing, signal generation |
| QA / DevOps | contract/E2E checks, workflows, signing, provenance | product/trading logic |
| Integrator / CTO | decomposition, file ownership, merge/conflicts | параллельную реализацию вместо назначенного владельца |

У одного файла в один момент времени один автор. Если vertical slice пересекает границы, сначала фиксируется contract, затем независимые части реализуются без параллельного редактирования одного файла.

## Внешняя документация и skills

- Для внешней library/SDK/API перед изменением проверять актуальную документацию через Context7, если он доступен.
- Для broker/exchange APIs, GitHub Actions, auth и money operations дополнительно сверяться с официальной документацией провайдера.
- Локальные accepted contracts/ADR не заменяются внешним примером без явного migration decision.
- Для специализированной задачи сначала искать готовый skill/plugin; новый meta-skill создавать только для реально повторяющейся предметной процедуры.

## Branch и PR flow

1. Один logical vertical slice → короткоживущая `feat/...`, `fix/...`, `agent/...` или `ops/...` branch от актуальной default branch.
2. До кода фиксируются scope, API/data contracts, error states и DoD. Для trading change — hypothesis, `tradable_at`, leakage controls, stage и metrics.
3. Один PR — один связный результат. Независимые product changes не добавляются «заодно».
4. Project Quality Gate обязателен для PR. Direct push не является способом обхода красной проверки.
5. После acceptance PR вливается в default branch. Долгоживущие конкурирующие release/UX branches с разной логикой запрещены.
6. Merge в default branch **не является production release**.

## Кумулятивная production delivery

Обычный путь:

1. Завершить и принять logical batch.
2. Merge accepted PR в default branch.
3. Явно запустить **Cumulative production release** и передать принятый `source_ref`.
4. Cumulative workflow запускает канонические `Deploy release to VPS` и `Android sideload APK` не более одного раза каждый для этого release.
5. Каждый canonical delivery workflow сохраняет свои safety/quality gates и exact-source provenance.
6. Docs/workflow-only merge не требует production release вообще.

Production deploy/APK не запускаются автоматически на каждый push. PR CI может проверять промежуточные commits — ограничение относится к дорогой production delivery.

Текущий cumulative orchestrator намеренно простой. Устранение повторного полного QA между orchestrator и canonical delivery и selective runtime delivery оформляется отдельной CI-задачей; это улучшение не должно возвращать auto-release on push.

Прямые server deploy / Android sideload разрешены для hotfix, rollback, forensic/ops или независимой поставки и должны быть явно обозначены как исключение.

## Vertical slice

1. **Contract / hypothesis.** Зафиксировать ожидаемый эффект, interface/data contract, failure states и DoD. Для Quant — source of edge и anti-leakage rules.
2. **Server behavior.** Canonical decisions/risk/lifecycle/audit живут server-side; thin client не содержит fallback-копию той же логики.
3. **Mobile.** Client показывает loading/empty/stale/error и не превращает observation в confirmed action.
4. **Tests.** Money/risk/execution — test-first/regressions для idempotency, failure modes и boundaries. Quant — OOS/walk-forward и leakage checks.
5. **QA.** Exact ref проходит secret scan, API/import checks, migrations, PostgreSQL pytest, Flutter analyze/test согласно project Quality Gate.
6. **Release.** Production delivery только после завершения logical batch через cumulative workflow либо явно обозначенный ops path.

## Trading promotion

Новая стратегия или существенное изменение admission/risk/exit проходит ADR `docs/adr/0001-live-promotion-gates.md`:

`Research → Backtest → OOS/Walk-forward → Shadow → Paper → Sandbox/Testnet → Canary Live → Scaled Live`.

Historical return не позволяет пропустить operational/security gate.

## Definition of Done

- API и mobile model согласованы по полям и states;
- migration graph и server tests проходят на canonical environment;
- Flutter analyze/test проходят для mobile changes;
- sensitive data не появляется в git/build constants/logs;
- money/execution operations имеют idempotency/failure/reconciliation regressions;
- trading logic имеет OOS evidence после realistic costs и anti-leakage checks;
- network/empty/stale/missing-data states видимы и не приводят к crash;
- critical Android flow проверяется на реальном устройстве, если это входит в scope;
- delivery имеет exact source provenance;
- Product owner принимает сценарий на текущем promotion stage; live требует отдельного owner decision.

## Sideload и VPS

Нормальная product delivery выполняется через **Cumulative production release**. Прямые `Android sideload APK` и `Deploy release to VPS` остаются canonical low-level workflows для targeted delivery/hotfix/rollback/ops и принимают явный `source_ref`.

Thin mode означает: server считает идеи и ведёт managed state/background jobs; Android отображает и передаёт owner decision. Локальный screener не является вторым source of truth.

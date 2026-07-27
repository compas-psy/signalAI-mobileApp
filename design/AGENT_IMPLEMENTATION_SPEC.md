# SignalAI v4 — ТЗ для coding-агента

## 0. Директива агенту

Доработать существующее Android-приложение SignalAI до персональной **операционной системы капитала**. Не создавать отдельный демонстрационный проект и не переписывать работающий продукт без необходимости.

Исходные материалы:

- текущий рабочий Flutter-код, описанный в `uploads/CURRENT_STATE.md`;
- исходный бренд и UI-токены из `SignalAI-Design-Spec.md`;
- целевой кликабельный прототип `index.html` из этого пакета;
- модель учёта `DOMAIN_LEDGER_SPEC.md`.

Главная смена модели: приложение управляет не набором сигналов, а всем капиталом владельца. Любая позиция, баланс, прибыль и статистика должны быть воспроизводимы из единой книги операций.

---

## 1. Цель продукта

Один владелец использует приложение для четырёх задач:

1. держит долгосрочный капитал в инвестиционных пакетах;
2. ведёт тактические позиции на недели и месяцы;
3. рискует отдельным бюджетом во фьючерсах, опционах и криптодеривативах;
4. видит полную историю того, откуда появился текущий капитал и какой результат дала каждая операция, позиция, стратегия и площадка.

Приложение обязано знать:

- все счета, кошельки и площадки;
- остатки денег и активов;
- пополнения, выводы и внутренние переводы;
- покупки, продажи, частичные исполнения и закрытия;
- комиссии, funding, проценты, дивиденды, купоны и налоги;
- текущие и закрытые позиции;
- реализованный и нереализованный P&L;
- стоимость капитала на любую историческую дату;
- принадлежность каждой позиции к контуру и пакету;
- торговую идею и решение, из которых возникла сделка;
- расхождения между собственной книгой и брокером.

---

## 2. Непереговорные продуктовые принципы

1. **Ledger first.** Экранные балансы и позиции являются проекциями книги операций, а не самостоятельными редактируемыми значениями.
2. **Никаких выдуманных данных.** Недоступные значения показываются как состояние с причиной.
3. **Каждое число проверяемо.** Из агрегата можно провалиться до операций, из которых он рассчитан.
4. **Денежные потоки не являются прибылью.** Пополнение и вывод не меняют P&L.
5. **Внутренний перевод не меняет совокупный капитал.** Он меняет только счёт, валюту, пакет или контур.
6. **Корректировки не переписывают историю.** Ошибка исправляется компенсирующей записью с причиной и audit trail.
7. **Broker snapshot не заменяет ledger.** Он используется для сверки и восстановления.
8. **Live-сделка всегда требует preview, risk check и биометрического подтверждения.**
9. **Статус заявки считается успешным только после broker acknowledgement.**
10. **Пустая выдача — допустимый результат.** Интерфейс объясняет, почему действий нет.

---

## 3. Технологическое решение

### 3.1. Не переписывать Flutter-приложение

Текущий продукт уже реализован на Flutter. Продолжать в существующем стеке. Прежнее упоминание Kotlin/Compose в ранней дизайн-спецификации считать устаревшим для этой кодовой базы.

### 3.2. Базовый стек

- Flutter stable / Dart stable.
- Android first, minSdk сохранить не ниже текущего; целевой SDK — актуальный на момент сборки.
- Feature-first архитектура с отдельными domain-модулями.
- SQLite с миграциями и транзакциями. Предпочтительно Drift; допустим другой зрелый typed SQLite layer при документированном обосновании.
- Secure storage + Android Keystore для секретов.
- `local_auth` или существующий нативный мост для биометрии.
- REST + WebSocket коннекторы отдельно от domain.
- Фоновые синхронизации через WorkManager-плагин или существующий нативный канал.
- Все денежные расчёты — decimal/fixed point. Не использовать `double` для денег, количества, комиссии, цены и P&L.
- Время хранить в UTC, отображать в выбранной зоне. Биржевую торговую дату хранить отдельно, где она отличается от календарной.

### 3.3. Разрешённые зависимости

Прежнее правило «ноль внешних пакетов» отменяется для инфраструктуры учёта. Финансовая корректность важнее искусственного отказа от зрелых библиотек.

Пакет допускается, если:

- активно поддерживается;
- не содержит рекламных/аналитических SDK;
- имеет понятную лицензию;
- не получает API-ключи и пользовательские данные;
- зафиксирован в lock-файле;
- критический расчёт всё равно покрыт собственными тестами.

Не подключать UI-kit, меняющий бренд-стиль. Графики и ключевые финансовые визуализации рисовать в существующем визуальном языке.

---

## 4. Целевая навигация

Пять верхнеуровневых разделов:

1. **Сегодня** — состояние капитала и решения.
2. **Капитал** — обзор, счета, пакеты, книга операций и аналитика.
3. **Торговля** — идеи, позиции, заявки, опционы и журнал сделок.
4. **Лаборатория** — стратегии, скринеры, бэктесты и сценарии.
5. **Контроль** — риск, сверка, интеграции, безопасность, уведомления и диагностика.

Миграция текущей навигации:

| Текущий раздел | Новый раздел |
|---|---|
| Идеи | Торговля → Идеи |
| Инвест | Капитал → Пакеты / Торговля → Тактика |
| Сделки | Торговля → Позиции / Журнал |
| Стратегии | Лаборатория |
| Настройки | Контроль |

Старые routes временно поддерживать через redirect, чтобы не ломать deep links и сохранённое состояние.

---

## 5. Adaptive layout: телефон и планшет

Использовать фактическую доступную ширину, не ориентацию.

### Compact: менее 600 dp

- нижняя навигация;
- одна колонка;
- detail открывается отдельным route или полноэкранным sheet;
- таблицы преобразуются в карточки;
- критические CTA закрепляются снизу, но не перекрывают контент;
- основные показатели помещаются в сетку 2×N.

### Medium: 600–900 dp

- NavigationRail без постоянных подписей либо с компактными подписями;
- dashboard 2 колонки;
- фильтры и summary слева, список/контент справа;
- подтверждение сделки — центрированный диалог 440–520 dp.

### Expanded: от 900 dp

- NavigationRail с подписями;
- master-detail для книги операций, пакетов, идей и журнала сделок;
- левая колонка 360–440 dp, detail занимает остаток;
- максимум рабочей области 1280 dp;
- не растягивать строки текста на всю ширину.

Тестовые размеры: 360×800, 390×844, 412×915, 800×1280, 1280×800 dp.

---

## 6. Бренд и дизайн-система

### 6.1. Цвета

```text
bg              #0B0B0D
surface         #141419
surfaceAlt      #101014
surfaceRaised   #17171C
border          #24242B
accent          #FFD400
profit/long     #2FD575
loss/short      #FF5C5C
info            #63A5FF
tactical        #B38CFF
textPrimary     #F2F2EF
textMuted       #9696A1
textFaint       #666670
warning         #FFB74D
```

Семантика:

- жёлтый — действие, фокус, требующее решения;
- зелёный/красный — только финансовый результат, направление и риск;
- синий — информационный/учётный статус;
- фиолетовый — тактический и риск-контур;
- оранжевый — сверка и неполные данные;
- никаких декоративных теней внутри интерфейса.

### 6.2. Типографика

- Jost 600: экранные заголовки, тикеры, wordmark.
- Manrope: основной UI.
- JetBrains Mono: цены, суммы, проценты, количество, время исполнения, IDs операций.
- Формат денег ru-RU: `8 420 600 ₽`, `−31 562,08 ₽`, `18,4 USDT`.
- Сравниваемые числа выравнивать по правому краю и использовать tabular figures.

### 6.3. Геометрия

- карточка 18 dp;
- внутренний блок 11–13 dp;
- кнопка 14 dp;
- dialog/sheet 22 dp;
- border 1 dp;
- screen padding 16–18 dp на телефоне, 22–28 dp на планшете;
- touch target минимум 44×44 dp.

### 6.4. Иконка

Сохранить исходный жёлтый бренд. Adaptive icon: фон `#17171C`, молния `#FFD400`, без текста внутри иконки.

---

## 7. Экран «Сегодня»

Главная задача — за 5–10 секунд ответить:

- сколько у меня всего капитала;
- что изменилось;
- сколько сейчас под риском;
- есть ли проблемы с данными;
- какие решения нужны сегодня.

### Блоки

1. Совокупный капитал и изменение за день/месяц/YTD.
2. Чистый P&L после комиссий, funding и известных налогов.
3. Аллокация Ядро / Тактика / Риск.
4. Свободный кэш и доступная маржа.
5. Открытый риск при одновременном срабатывании стопов.
6. Нужные решения:
   - подтверждение идеи;
   - распределение новых денег;
   - ребалансировка;
   - экспирация опциона;
   - позиция без защиты;
   - расхождение ledger/broker;
   - превышение лимита.
7. P&L decomposition: рост стоимости, realized, дивиденды/купоны, funding, комиссии, налоги.
8. Состояние Risk Engine и сверки.

Каждая KPI-карточка должна открывать детализацию расчёта.

---

## 8. Раздел «Капитал»

Подразделы:

- Обзор.
- Счета.
- Пакеты.
- Журнал операций.
- Аналитика.

### 8.1. Обзор

Показывать отдельно:

- текущую стоимость;
- чисто внесённый капитал;
- накопленный инвестиционный результат;
- realized P&L;
- unrealized P&L;
- доходы: дивиденды, купоны, проценты, funding;
- расходы: комиссии, funding, налоги;
- капитал по счетам, валютам, классам активов, контурам и пакетам;
- TWR и MWR/XIRR, потому что пользователь пополняет и выводит деньги.

### 8.2. Счета

Для каждого счёта:

- площадка и тип;
- валюта учёта;
- equity, cash, collateral, blocked funds;
- positions market value;
- realized/unrealized P&L;
- дата последней синхронизации;
- статус сверки;
- разрешения API: read/trade/withdraw;
- риск контрагента и собственный лимит площадки.

Поддержать:

- брокерский счёт;
- ИИС, если используется;
- криптобиржу;
- внешний кошелёк;
- банковский резерв;
- ручной счёт;
- виртуальный счёт для paper trading.

### 8.3. Пакеты

Пакет — не отдельный брокерский счёт, а логическая корзина поверх ledger.

Карточка пакета:

- цель и горизонт;
- контур;
- фактический и целевой вес;
- стоимость и P&L;
- состав и диапазоны весов;
- вклад активов в результат и риск;
- история пополнений и выводов;
- benchmark;
- тезис и invalidation rules;
- предложения по ребалансировке.

Одна позиция может быть распределена по нескольким пакетам только через явные виртуальные лоты. Сумма package allocations обязана равняться позиции.

### 8.4. Журнал операций / Capital Ledger

Это главный источник истины.

В списке показывать:

- дата и время;
- инструмент или денежный счёт;
- тип операции;
- источник;
- количество/цена;
- cash impact;
- P&L impact;
- пакет и контур;
- статус сверки.

Фильтры:

- период;
- счёт;
- площадка;
- валюта;
- инструмент;
- тип события;
- пакет;
- контур;
- broker/manual/system;
- сверено/расхождение/ожидает.

Detail операции:

- immutable ID;
- source event ID;
- broker order/fill IDs;
- effective time, received time, created time;
- количество, цена, валюта;
- комиссия и налог;
- cash impact;
- cost basis impact;
- realized P&L impact;
- lot allocation;
- связанные order, position, trade, idea, package;
- история импорта и сверки;
- корректирующие операции.

Действия:

- добавить ручную операцию;
- привязать к пакету/сделке;
- создать корректировку;
- отметить дубль;
- экспортировать CSV/JSON;
- открыть raw broker payload в режиме диагностики.

Удаление финансовой операции запрещено. Для ошибочной ручной записи создавать reversal/correction.

### 8.5. Аналитика капитала

Минимум:

- капитал во времени;
- TWR и MWR/XIRR;
- realized/unrealized/income/cost decomposition;
- результат по счёту, пакету, классу актива, валюте;
- contribution to return;
- drawdown;
- cashflows;
- комиссии и funding во времени;
- currency P&L отдельно от local-price P&L;
- benchmark comparison.

---

## 9. Раздел «Торговля»

Подразделы:

- Позиции.
- Идеи.
- Заявки.
- Опционы.
- Журнал сделок.

### 9.1. Позиции

Для позиции показывать:

- инструмент, venue, direction;
- количество и multiplier;
- entry average и current price;
- market value/notional;
- margin/collateral;
- liquidation price для криптодеривативов;
- realized/unrealized P&L;
- fees/funding;
- stop, targets, max loss;
- принадлежность стратегии, сделке, пакету и контуру;
- состояние broker reconciliation.

Не смешивать позицию и сделку:

- позиция — текущий агрегат по инструменту и счёту;
- сделка — пользовательский замысел и полный lifecycle от идеи до разбора;
- одна сделка может иметь несколько orders/fills и несколько ног;
- одна брокерская позиция может содержать лоты нескольких сделок, если пользователь это явно разрешил.

### 9.2. Идеи

Сохранить сильные элементы текущего продукта:

- score;
- причины;
- entry/invalidation/targets;
- статистика факторов;
- horizon;
- data freshness;
- portfolio impact;
- risk checks;
- expiry идеи.

Перед подтверждением показывать изменение:

- open risk;
- margin;
- валютной и факторной экспозиции;
- корреляции с имеющимися позициями;
- loss limits;
- package/contour budget.

### 9.3. Заявки и исполнения

Order lifecycle:

`DRAFT → PREVIEWED → CONFIRMED → SUBMITTING → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED`.

Ошибочные состояния:

`REJECTED`, `CANCEL_PENDING`, `CANCELLED`, `UNKNOWN`, `RECONCILIATION_REQUIRED`.

Каждый fill автоматически порождает ledger event. Частичный fill не должен ждать полного исполнения.

### 9.4. Опционы

Отдельная сущность `OptionStructure` с ногами.

Первая версия:

- long call/put;
- covered call;
- protective put;
- bull call spread;
- bear put spread;
- collar.

Показывать:

- payoff;
- current P&L;
- max profit/loss;
- break-even;
- Greeks;
- IV rank/percentile;
- bid/ask и liquidity warning;
- DTE;
- plan до экспирации;
- exercise/assignment/expiration events в ledger.

Naked short options отключены по умолчанию и включаются только отдельным risk permission.

### 9.5. Журнал сделок

Trade journal — аналитическая надстройка над ledger, а не второй независимый учёт.

Запись сделки объединяет:

- исходную идею;
- plan versions;
- решения и подтверждения;
- orders и fills;
- изменения stop/targets;
- связанные ledger events;
- итоговый net P&L;
- результат в R;
- MAE/MFE;
- комиссии и funding;
- причина выхода;
- дисциплина относительно плана;
- текстовые заметки, эмоции, теги, скриншоты.

Метрики:

- win rate;
- profit factor;
- expectancy;
- average win/loss;
- R distribution;
- drawdown;
- result by strategy/instrument/venue/day/time/market regime;
- plan adherence;
- early entry, late exit, stop widening, over-sizing;
- результат до и после издержек.

---

## 10. Единая книга капитала

Реализация подробно описана в `DOMAIN_LEDGER_SPEC.md`. Ключевое требование:

```text
External events + Manual events
              ↓
        Immutable Ledger
              ↓
   Deterministic projections
              ↓
Balances · Lots · Positions · P&L · Packages · Analytics
              ↓
      Broker reconciliation
```

Нельзя обновлять balance/position напрямую из UI или broker snapshot. Snapshot создаёт reconciliation observation; отсутствующие подтверждённые события импортируются в ledger.

---

## 11. P&L и доходность

### 11.1. Базовые представления

- Gross realized P&L.
- Net realized P&L после fees/funding/taxes where known.
- Unrealized P&L.
- Investment income.
- External cashflow.
- Internal transfer.
- Total equity.

### 11.2. Доходность

Показывать два метода:

- TWR — качество управления без влияния пополнений;
- MWR/XIRR — фактическая доходность денег владельца.

Простой процент `P&L / текущий капитал` не использовать как основную доходность при наличии потоков.

### 11.3. Базовая валюта

Базовая валюта пользователя — RUB. Оригинальную сумму и валюту сохранять всегда.

Для каждого пересчёта хранить:

- FX source;
- rate;
- timestamp;
- valuation policy.

Разделять результат актива и валютный результат, где возможно.

---

## 12. Reconciliation

Сверять минимум:

- cash balances;
- asset quantities;
- open positions;
- open orders;
- fees/funding;
- realized P&L, если площадка предоставляет;
- corporate actions;
- margin/collateral.

Статусы:

- `MATCHED`;
- `PENDING_EVENTS`;
- `MISMATCH`;
- `STALE_SNAPSHOT`;
- `SOURCE_UNAVAILABLE`;
- `MANUAL_ACCOUNT`.

При mismatch:

1. показать конкретную разницу;
2. попытаться импортировать пропущенные события;
3. предложить manual resolution;
4. сохранить resolution decision;
5. блокировать только действия, для которых расхождение критично.

При неизвестном статусе заявки новые сделки по этому инструменту блокируются до сверки.

---

## 13. Risk Engine

Иерархия:

`весь капитал → контур → площадка → класс актива → пакет → стратегия → инструмент → сделка`.

Проверки:

- risk per trade;
- total open risk;
- day/week/month loss;
- drawdown;
- margin;
- liquidation distance;
- premium at risk;
- short gamma;
- concentration;
- correlated exposure;
- currency exposure;
- event risk;
- consecutive losses;
- data/reconciliation health;
- counterparty limit.

Режимы:

- `NORMAL`;
- `CAUTION` — уменьшать suggested size;
- `REDUCE_ONLY`;
- `KILL_SWITCH`.

Risk Engine возвращает не boolean, а решение:

```dart
class RiskDecision {
  final RiskMode mode;
  final bool allowed;
  final Decimal? maxAllowedQuantity;
  final List<RiskViolation> blocking;
  final List<RiskWarning> warnings;
  final PortfolioImpact impact;
}
```

UI обязан показать человеческое объяснение каждого ограничения.

---

## 14. Раздел «Лаборатория»

Содержит:

- пакеты стратегий;
- параметры;
- scanner results;
- backtest;
- walk-forward;
- factor contribution;
- сценарии портфеля;
- stress tests;
- paper/live comparison.

Бэктест обязан учитывать:

- комиссии;
- проскальзывание;
- funding;
- rollover фьючерсов;
- delisting/survivorship bias, где применимо;
- реальные contract multipliers;
- валютную переоценку.

Результат эксперимента не изменяет live-конфигурацию без отдельного review и подтверждения.

---

## 15. Раздел «Контроль»

Подразделы:

- Risk Engine.
- Сверка.
- Интеграции.
- Уведомления.
- Безопасность.
- Диагностика.
- Audit log.

Обязательные действия:

- kill switch максимум в два тапа;
- отключить live trading;
- перепроверить API permissions;
- запустить полную сверку;
- экспортировать audit trail;
- восстановить локальную БД из зашифрованного backup;
- проверить данные и trading readiness.

---

## 16. AI Copilot

Copilot получает только структурированные проекции и ссылки на источники.

Разрешено:

- объяснять капитал и P&L;
- отвечать «почему изменилась стоимость»;
- находить повторяющиеся ошибки журнала;
- предлагать распределение пополнения;
- составлять trade preview;
- объяснять risk block;
- подготавливать варианты ребалансировки;
- формировать weekly/monthly review.

Запрещено:

- создавать финансовые значения без источника;
- отправлять заявку без стандартного confirm flow;
- менять ledger;
- автоматически снимать risk limit;
- скрывать противоречащие данные.

Каждый ответ Copilot должен иметь блок `Основано на` с датой данных и сущностями.

---

## 17. Безопасность

- API withdrawal permission считается критической ошибкой конфигурации и не требуется приложению.
- API keys — только secure storage/Keystore.
- Биометрия для live enable, отправки заявки, изменения лимитов и экспорта чувствительных данных.
- Screen lock при возврате из background после configurable timeout.
- Секреты и raw payload не попадают в logs/crash reports.
- Локальная БД и backups шифруются.
- Audit trail для изменения лимитов, manual operations, corrections, confirmations и reconciliation resolutions.
- Read-only mode после новой установки или восстановления.

---

## 18. Offline и надёжность

Различать:

- offline;
- stale quotes;
- broker unavailable;
- partial sync;
- background sync delayed;
- order status unknown;
- ledger mismatch;
- FX rate unavailable;
- manual account stale.

Для каждого состояния:

- человекочитаемая причина;
- timestamp последних достоверных данных;
- влияние на расчёты;
- recovery action;
- перечень заблокированных функций.

---

## 19. Рекомендуемая структура модулей

```text
lib/
  app/
  core/
    design_system/
    decimal/
    time/
    security/
    database/
    networking/
    diagnostics/
  domain/
    ledger/
    accounts/
    instruments/
    portfolio/
    packages/
    orders/
    positions/
    trades/
    options/
    pnl/
    risk/
    reconciliation/
    analytics/
  integrations/
    tinkoff/
    moex/
    bybit/
    manual/
  features/
    today/
    capital/
    trading/
    lab/
    control/
```

Domain не импортирует Flutter UI и DTO конкретного брокера.

---

## 20. Порядок реализации

### Phase 0 — Инвентаризация и защита текущего продукта

- создать branch;
- зафиксировать screenshot/golden tests текущих экранов;
- описать текущие models, storage и routes;
- сделать backup/migration strategy;
- не ломать работающий signal engine и биржевое исполнение.

### Phase 1 — Ledger foundation

- decimal money types;
- SQLite schema и migrations;
- accounts/instruments/currencies;
- immutable ledger events;
- opening balances;
- ручные операции;
- deterministic balances/lots/positions;
- базовый reconciliation;
- импорт существующей истории.

### Phase 2 — Новый adaptive shell и «Сегодня»

- новые top-level routes;
- phone/tablet layouts;
- общий капитал;
- контуры;
- P&L decomposition;
- decisions;
- data/reconciliation status.

### Phase 3 — Капитал

- accounts;
- packages;
- capital ledger UI;
- cashflows;
- analytics;
- TWR/MWR;
- export.

### Phase 4 — Trading lifecycle

- связать существующие идеи с Trade/Order/Fill/Ledger;
- positions projection;
- trade journal;
- plan versions;
- MAE/MFE;
- fees/funding;
- confirm flow и risk impact.

### Phase 5 — Options и расширенный Risk Engine

- option instruments/legs/structures;
- payoff/Greeks/scenarios;
- expiration workflow;
- exposure aggregation;
- correlation/concentration limits.

### Phase 6 — AI Copilot и reviews

- grounded Q&A;
- journal patterns;
- weekly/monthly review;
- capital allocation proposals.

Каждая phase должна завершаться миграцией, тестами и рабочей сборкой. Не вести несколько незавершённых фаз параллельно.

---

## 21. Тестирование финансовой корректности

Обязательные unit/property tests:

- покупка → частичная продажа → полная продажа;
- несколько лотов по разным ценам;
- комиссия в валюте сделки и отдельной валюте;
- split/dividend/coupon;
- deposit/withdraw/internal transfer;
- FX conversion;
- long/short futures;
- daily variation margin;
- perpetual funding;
- option premium, expiration, exercise, assignment;
- partial fill и отмена остатка;
- duplicate broker event;
- out-of-order event;
- correction/reversal;
- timezone/trading date boundary;
- broker snapshot mismatch;
- negative cash and borrowed asset;
- TWR с несколькими потоками;
- XIRR/MWR.

Golden/screenshot tests:

- phone и tablet для пяти разделов;
- ledger list/detail;
- trade journal detail;
- mismatch state;
- stale data;
- empty state;
- confirm sheet/dialog;
- kill switch.

---

## 22. Definition of Done

Функция считается готовой, когда:

1. UI соответствует `index.html` и токенам.
2. Работает на телефоне и планшете без простого растягивания.
3. Все суммы воспроизводимы из ledger events.
4. Есть drill-down от KPI до конкретных операций.
5. Пополнения/выводы не искажают P&L.
6. Reconciliation показывает конкретные расхождения.
7. Исправление не удаляет историю.
8. Live order проходит risk check, preview, biometric и broker acknowledgement.
9. Расчёты покрыты тестами и не используют binary floating point.
10. Миграция не теряет текущие идеи, настройки и журнал.
11. При ошибке приложение показывает причину, время данных и recovery action.
12. Сборка проходит static analysis, tests и release build.

---

## 23. Формат отчёта coding-агента

После каждого этапа агент обязан вернуть:

- что реализовано;
- какие файлы и схемы изменены;
- какие migrations добавлены;
- какие тесты написаны и их результат;
- какие ограничения остались;
- как вручную проверить функцию;
- screenshot phone/tablet;
- риски следующего этапа.

Не заявлять о завершении, если используются mock-данные в production flow, расчёт не воспроизводим или reconciliation не пройден.

# Контракт мобильного гейтвея (Server B → приложение)

Базовый адрес задаётся при сборке: `--dart-define=SIGNALAI_API_BASE_URL=https://…`
Только HTTPS — клиент отказывается работать по http.

Аутентификация business API: `Authorization: Bearer <активный токен устройства>`.
Политика сервера только `active_only` и по умолчанию fail-closed: bootstrap
`SIGNALAI_DEVICE_TOKEN` допустим лишь в `POST /api/v1/device-enrollment/pair`
и там обязательно дополняется `X-Pairing-Session-Id`: отдельным
owner-provisioned high-entropy значением с UTC expiry и max-uses (default 1,
bounded). Сервер хранит только verifier session и блокирует счётчик использований
в транзакции; отсутствие, истечение или исчерпание закрывает pairing.
Успешный pair возвращает единственный раз новый 256-bit token конкретного
device generation; сервер сохраняет только verifier. `POST /rotate` с активным
token немедленно отзывает старый, `POST /revoke` закрывает текущий token.

`/pair` требует `X-Idempotency-Key`, `X-Pairing-Session-Id` и JSON `{ "device_id": "…", "metadata":
{ "label": "…", "platform": "android", "app_version": "…" } }`. Metadata
allowlisted и ограничена по размеру. Повтор завершённого ключа возвращает 409:
raw token не хранится и не может быть выдан повторно.

Идемпотентность: pairing и rotation требуют `X-Idempotency-Key`; повтор не
создаёт второй token. Команды исполнения используют свой независимый ключ (ТЗ §7).
`POST /revoke` отвечает `{"status":"revoked"}` либо явным
`{"status":"already_revoked"}` — только после одного из этих исходов телефон
стирает свой Keystore bearer. `POST /revoke-lost` принимает `{device_id,
generation}` и доступен только другому active owner device.

Ошибки: `{"error": {"message": "текст для человека", "code": "…"}}` с кодом 4xx/5xx.
`401/403` — устройство не авторизовано, `409` — сигнал больше не актуален.

Все денежные значения — числа, все готовые к показу подписи — строки. Сервер
форматирует то, что зависит от инструмента (цены, изменения), клиент — только то,
что зависит от настроек риска.

---

## GET /v1/digest

Утренний дайджест: режим рынка, события, идеи дня.

```json
{
  "title": "Утренний дайджест",
  "subtitle": "Сб, 25 июля · 10:10 МСК",
  "delivery_badges": ["TG ✓", "MAX ✓"],
  "regime": [
    {"name": "IMOEX", "value": "−0,42%", "tone": "negative"},
    {"name": "BTC", "value": "+1,84%", "tone": "positive"},
    {"name": "RVI", "value": "28,4", "tone": "neutral"}
  ],
  "regime_note": "Режим смешанный: рубль слабеет…",
  "events": [
    {"time": "16:30", "text": "Решение ЦБ по ключевой ставке",
     "impact": "high", "affects": "MXU6 · Si"}
  ],
  "signals_quota": "5 из 5",
  "signals": [ /* см. объект сигнала ниже */ ]
}
```

`tone`: `positive` | `negative` | `neutral` | `accent`.
`impact`: `high` | `mid` | `low` — цвет маркера события.

### Объект сигнала

```json
{
  "id": "si",
  "symbol": "SiU6",
  "name": "Доллар/Рубль · сент 2026",
  "market": "FORTS",
  "direction": "long",
  "horizon": "swing",
  "horizon_label": "1–3 дня",
  "score": 87,
  "entry": 87450,
  "stop_loss": 86900,
  "take_profits": [
    {"index": 1, "price": 88200, "share_percent": 50},
    {"index": 2, "price": 88700, "share_percent": 30},
    {"index": 3, "price": 89400, "share_percent": 20}
  ],
  "price_decimals": 0,
  "risk_reward": "2,3",
  "chips": ["Spring", "OB 1H", "RSI-див", "OI +4,2%"],
  "note": "Спринг из фазы C накопления по Вайкоффу…",
  "factors": [
    {"name": "Wyckoff", "text": "Фаза C накопления, spring 87 050…", "weight": 3}
  ],
  "events": [
    {"time": "14:00", "text": "Дневной клиринг FORTS", "impact": "low"}
  ],
  "unit_risk": 550,
  "unit_risk_label": "550 ₽ / контракт",
  "unit_multiplier": 1,
  "unit_decimals": 0,
  "unit_name": "конт.",
  "last_price": "87 480",
  "change_label": "+0,31%",
  "change_up": true,
  "status": "pushed",
  "valid_until": "2026-07-25T15:00:00Z",
  "invalidation_price": 87300,
  "correlation_group": "currency",
  "strategy_id": "s1"
}
```

Пояснения к полям, которые легко понять неверно:

* `market` — `FORTS` | `MOEX` | `CRYPTO`; показывается в подзаголовке карточки.
* `horizon` — `intraday` | `swing` (ТЗ §1). Влияет на сопровождение на сервере.
* `score` — SignalScore 0–100 (ТЗ §5.3); рисуется кольцом вокруг номера.
* `weight` фактора — 1–3, длина полоски (33/67/100%).
* `unit_risk` — сколько рублей теряется на одной единице при срабатывании стопа.
  Объём считается как `floor(риск_в_рублях / unit_risk)`.
* `unit_multiplier` / `unit_decimals` / `unit_name` — как показать объём:
  для фьючерсов `1 / 0 / "конт."`, для BTC `0.01 / 2 / "BTC"`.
* `status` — статус-машина ТЗ §5.4: `proposed` | `pushed` | `confirmed` |
  `working` | `open` | `closed` | `invalidated` | `expired` | `rejected`.
  Кнопка подтверждения активна только при `proposed` и `pushed`.
* `valid_until` — ISO-8601 UTC. Просроченный сигнал сервер обязан отдавать со
  статусом `expired`.

## GET /v1/trades

```json
{
  "equity_title": "Эквити · 30 дней",
  "equity_change": "+8,4%",
  "equity_curve": [0, 0.4, 0.2, "…"],
  "stats": [{"value": "61%", "label": "винрейт", "tone": "neutral"}],
  "positions": [
    {"symbol": "SiU6", "direction": "long", "entry_label": "87 450",
     "current_label": "87 890", "pnl_label": "+14 080 ₽", "pnl_positive": true,
     "progress_percent": 59, "stage": "До TP1 осталось 310 п. · SL на месте"}
  ],
  "journal": [
    {"date": "24.07", "symbol": "SiU6", "direction": "long",
     "outcome": "TP2", "r_multiple": 2.1}
  ]
}
```

`progress_percent` — прогресс до ближайшего тейка, 0–100.

## GET /v1/strategies

```json
{
  "packs": [
    {"id": "s1", "name": "Интеграционная · MOEX FORTS",
     "description": "Wyckoff + SMC + Price Action…",
     "stats_label": "WR 61% · PF 2,1 · 38 сделок / 90д",
     "enabled": true, "mode": "live", "horizon": "swing",
     "version": 4, "closed_paper_trades": 38}
  ],
  "params_title": "Параметры · Интеграционная",
  "params": [{"name": "Мин. R:R до TP2", "value": "2,0"}],
  "backtest": {
    "info": "Последний прогон · 180 дней · 214 сделок",
    "stats": [{"value": "+41%", "label": "доходность", "tone": "positive"}],
    "equity_curve": [0, 0.5, 0.2, "…"]
  }
}
```

`mode` — `paper` | `live`; `closed_paper_trades` нужен для правила «в live
только после 20 закрытых бумажных сделок» (ТЗ §1).

## GET /v1/settings

```json
{
  "exchanges": [
    {"id": "tinvest", "abbr": "T", "name": "Т-Инвестиции API",
     "subtitle": "MOEX: фьючерсы и акции · токен активен",
     "connected": true, "accent": "#FFD400"}
  ],
  "channels": [
    {"id": "telegram", "name": "Telegram-бот",
     "subtitle": "@signalai_bot · идеи + алерты TP/SL", "enabled": true}
  ],
  "notifications": [
    {"id": "digest", "name": "Утренний дайджест",
     "subtitle": "Будни · 10:10 МСК · топ-5 идей", "enabled": true}
  ],
  "risk": {
    "deposit": 2400000,
    "risk_percent": 0.75,
    "daily_loss_limit": "−2% · автостоп",
    "max_concurrent_trades": "до 3",
    "pause_rule": "пауза до завтра"
  }
}
```

`deposit` приходит из брокерского API (ТЗ §6.1) — клиент его не выдумывает,
но позволяет задать вручную, если сервер отдаёт его как настраиваемый.

## POST /v1/signals/{id}/confirm

Подтверждение сделки. Вызывается после биометрии.
Сервер проверяет актуальность сигнала, резервирует маржу, выставляет лимитку и
OCO-связку (ТЗ §6.3, §7).

Заголовок: `X-Idempotency-Key: confirm:{id}`.

Ответ `200 {}`. Ошибки: `409` — сигнал просрочен/инвалидирован, `422` — не хватает
маржи (в `message` — что именно).

## Остальные команды

| Метод | Путь | Тело | Назначение |
|---|---|---|---|
| POST | `/v1/strategies/{id}/enabled` | `{"enabled": true}` | вкл/выкл пакет |
| POST | `/v1/strategies/{id}/backtest` | — | прогон, ответ — объект `backtest` |
| POST | `/v1/exchanges/{id}/connect` | — | ответ — объект биржи |
| POST | `/v1/channels/{id}` | `{"enabled": true}` | канал доставки |
| POST | `/v1/notifications/{id}` | `{"enabled": true}` | правило уведомлений |
| PATCH | `/v1/risk-profile` | `{"deposit": …, "risk_percent": …}` | ответ — объект `risk` |

## Демо-режим

Если `SIGNALAI_API_BASE_URL` не задан, приложение поднимается на данных макета
(`lib/data/mock/demo_repository.dart`) и работает офлайн. Это удобно для
разработки интерфейса, но ни одна цифра там не является рыночной.

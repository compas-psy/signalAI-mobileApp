# SignalAI Engine

Реализация `SIGNAL_AI_TRADING_ENGINE_TZ_v1.0`: инвестиционный и торговый
движок, аналитика и paper trading. Боевые заявки по умолчанию закрыты
(`risk.paper_only: true`).

## Запуск одной командой

```bash
docker compose up -d          # postgres + redis + api
```

Без Docker:

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
export SIGNALAI_DATABASE_URL=postgresql+psycopg://signalai:PASS@127.0.0.1:5432/signalai
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Проверка: `curl localhost:8000/health` — отдаёт отпечаток конфигурации,
режим исполнения и состояние аварийной остановки.

Схема API: `http://localhost:8000/docs`.

## Тесты

```bash
.venv/bin/python -m pytest
```

Тесты идут против **настоящего PostgreSQL**: половина проверяемого (триггеры
append-only, NUMERIC без потери точности, CHECK по направлению сделки) в
SQLite отсутствует.

## Что где

| Каталог | Что внутри |
|---|---|
| `config/default.yaml` | все параметры торговой логики (§0.8, §27) |
| `app/models/` | модель данных §22 |
| `app/api/v1/` | контракт §23 |
| `app/detectors/` | детекторы структуры, Вайкоффа, SMC, PA (§8) |
| `app/strategies/` | три стратегии §10–§12 |
| `app/scoring/` | оценка §15.1 и вероятность §15.3 |
| `app/risk/` | лимиты и размер позиции §17 |
| `alembic/` | миграции |

## Правила, которые здесь не обсуждаются

- Все времена в UTC (§4.4).
- Все деньги и цены — `NUMERIC`/`Decimal`, никогда `float` (UX-ТЗ §17.1).
- Незакрытая свеча не используется как закрытая (§4.4).
- Журнал переходов и аудит только дополняются — запрет стоит триггером базы.
- Секреты только из окружения, в git и в логи не попадают (§21).
- LLM объясняет готовый JSON и не вычисляет ни одного торгового числа (§0.4).

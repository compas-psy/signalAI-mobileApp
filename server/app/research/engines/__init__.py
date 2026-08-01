"""Движки ранних сигналов (ТЗ Early Signals §13).

Девять движков по ТЗ. Все — чистые функции: сеть, модели и «последние»
данные им недоступны по построению, иначе результат нельзя воспроизвести
(§2.7), а значит нельзя и проверить на истории.

Данные для них приходят снаружи, и сегодня почти всех источников ещё нет:
движок написан и проверен, но кормить его нечем, пока не подключён адаптер.
Это честное состояние, а не заглушка — правовой шлюз называет по каждому
источнику, что именно мешает.
"""

from . import (
    budget,
    capacity,
    crypto_econ,
    crypto_supply,
    demand,
    hiring,
    spread,
    supplier,
    wcq,
)

# Все девять, в порядке разделов ТЗ.
ENGINES = (
    wcq,
    demand,
    hiring,
    spread,
    supplier,
    budget,
    capacity,
    crypto_econ,
    crypto_supply,
)

__all__ = [
    "ENGINES",
    "budget",
    "capacity",
    "crypto_econ",
    "crypto_supply",
    "demand",
    "hiring",
    "spread",
    "supplier",
    "wcq",
]

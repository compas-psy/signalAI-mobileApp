"""Конфигурация движка и её отпечаток.

Engine-ТЗ §0.8 требует, чтобы все параметры торговой логики жили в
конфигурации, а §27 — чтобы каждая идея и каждый прогон бэктеста хранили
``config_hash``. Без этого нельзя ответить на вопрос «при каких порогах
получена вот эта идея», а значит нельзя ни воспроизвести её, ни оспорить.

Секреты сюда не попадают: подключения и ключи приходят из переменных
окружения (§21, §23 UX-ТЗ), а конфигурация лежит в git и читается всеми.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DEFAULT_CONFIG = CONFIG_DIR / "default.yaml"

# Отличает «значение по умолчанию не задано» от «задано None».
_MISSING = object()


class ConfigError(RuntimeError):
    """Конфигурация не читается или противоречива."""


def _canonical(value: Any) -> str:
    """Стабильное представление для хэша.

    Ключи сортируются, пробелы фиксированы: перестановка строк в YAML не
    должна менять отпечаток, иначе он перестанет означать «те же числа».
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class EngineConfig:
    """Прочитанная конфигурация вместе с её отпечатком."""

    data: dict[str, Any]
    config_hash: str
    source: str

    def section(self, name: str) -> dict[str, Any]:
        try:
            value = self.data[name]
        except KeyError as exc:  # pragma: no cover - защита от опечатки в yaml
            raise ConfigError(f"в конфигурации нет раздела {name!r}") from exc
        if not isinstance(value, dict):
            raise ConfigError(f"раздел {name!r} должен быть словарём")
        return value

    def get(self, path: str, default: Any = _MISSING) -> Any:
        """Достать значение по пути вида ``risk.daily_loss_limit``.

        Отсутствие значения — ошибка, а не тихий ``None``: параметр, которого
        нет, означает, что торговая логика посчитает не то, что задумано.
        """
        node: Any = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                if default is not _MISSING:
                    return default
                raise ConfigError(f"в конфигурации нет параметра {path!r}")
            node = node[part]
        return node

    def decimal(self, path: str) -> Decimal:
        """Денежная или долевая величина как ``Decimal``.

        UX-ТЗ §17.1: «Все суммы сохраняются decimal, не float». Через строку —
        иначе ``Decimal(0.005)`` даст двоичный хвост.
        """
        return Decimal(str(self.get(path)))


def _validate(data: dict[str, Any]) -> None:
    """Проверки, которые дешевле сделать при загрузке, чем при сделке."""
    weights = data.get("scoring", {}).get("weights", {})
    if not weights:
        raise ConfigError("scoring.weights пуст — оценка не сможет посчитаться")
    total = sum(Decimal(str(v)) for v in weights.values())
    if total != Decimal("1"):
        raise ConfigError(
            f"сумма scoring.weights = {total}, должна быть ровно 1 "
            "(engine-ТЗ §15.1; data_quality не складывается, а умножает)"
        )

    risk = data.get("risk", {})
    base = Decimal(str(risk.get("base_risk_per_trade", 0)))
    top = Decimal(str(risk.get("max_risk_per_trade", 0)))
    if base <= 0 or top < base:
        raise ConfigError(
            "risk.base_risk_per_trade должен быть положителен и не больше "
            "risk.max_risk_per_trade"
        )

    daily = Decimal(str(risk.get("daily_loss_limit", 0)))
    weekly = Decimal(str(risk.get("weekly_loss_limit", 0)))
    monthly = Decimal(str(risk.get("monthly_loss_limit", 0)))
    if not (daily < weekly < monthly):
        raise ConfigError(
            "лимиты потерь должны расти: дневной < недельный < месячный "
            f"(сейчас {daily} / {weekly} / {monthly})"
        )

    ladder = risk.get("drawdown_scaling", [])
    if not ladder:
        raise ConfigError("risk.drawdown_scaling пуст — просадка не будет резать размер")
    bounds = [Decimal(str(step["max_dd"])) for step in ladder]
    if bounds != sorted(bounds):
        raise ConfigError("risk.drawdown_scaling должен идти по возрастанию max_dd")
    if Decimal(str(ladder[-1]["multiplier"])) != 0:
        raise ConfigError(
            "последняя ступень risk.drawdown_scaling обязана останавливать "
            "торговлю (multiplier: 0) — engine-ТЗ §17"
        )


def load_config(path: str | os.PathLike[str] | None = None) -> EngineConfig:
    """Прочитать конфигурацию и посчитать её отпечаток."""
    source = Path(path or os.environ.get("SIGNALAI_CONFIG") or DEFAULT_CONFIG)
    if not source.is_file():
        raise ConfigError(f"файл конфигурации не найден: {source}")
    try:
        data = yaml.safe_load(source.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{source}: не разбирается как YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{source}: ожидался словарь на верхнем уровне")

    _validate(data)
    digest = hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()
    return EngineConfig(data=data, config_hash=digest, source=str(source))


@lru_cache(maxsize=4)
def get_config(path: str | None = None) -> EngineConfig:
    """Кэшированная конфигурация процесса."""
    return load_config(path)

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
_MISSING = object()


class ConfigError(RuntimeError):
    """Конфигурация не читается или противоречива."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class EngineConfig:
    data: dict[str, Any]
    config_hash: str
    source: str

    def section(self, name: str) -> dict[str, Any]:
        try:
            value = self.data[name]
        except KeyError as exc:
            raise ConfigError(f"в конфигурации нет раздела {name!r}") from exc
        if not isinstance(value, dict):
            raise ConfigError(f"раздел {name!r} должен быть словарём")
        return value

    def get(self, path: str, default: Any = _MISSING) -> Any:
        node: Any = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                if default is not _MISSING:
                    return default
                raise ConfigError(f"в конфигурации нет параметра {path!r}")
            node = node[part]
        return node

    def decimal(self, path: str) -> Decimal:
        return Decimal(str(self.get(path)))


def _validate_shares(raw: Any, *, label: str) -> None:
    if not isinstance(raw, list) or len(raw) < 3:
        raise ConfigError(f"{label}: нужны как минимум три доли")
    values = [Decimal(str(value)) for value in raw]
    if any(value <= 0 for value in values):
        raise ConfigError(f"{label}: все доли должны быть > 0")
    if sum(values, Decimal(0)) != Decimal(1):
        raise ConfigError(f"{label}: сумма долей должна быть ровно 1")


def _validate_risk_management(risk: dict[str, Any]) -> None:
    management = risk.get("management", {})
    profiles = management.get("profiles", {})
    required = {"defensive", "balanced", "conviction"}
    if set(profiles) != required:
        raise ConfigError(
            "risk.management.profiles должен содержать ровно defensive/balanced/conviction"
        )
    for mode, profile in profiles.items():
        multiplier = Decimal(str(profile.get("risk_multiplier", 0)))
        if not (Decimal(0) < multiplier <= Decimal(1)):
            raise ConfigError(
                f"risk.management.profiles.{mode}.risk_multiplier обязан быть в (0,1]"
            )
        _validate_shares(profile.get("tp_shares"), label=f"profiles.{mode}.tp_shares")
        giveback = Decimal(str(profile.get("mfe_giveback", -1)))
        if not (Decimal(0) < giveback < Decimal(1)):
            raise ConfigError(f"profiles.{mode}.mfe_giveback обязан быть между 0 и 1")
        if Decimal(str(profile.get("atr_multiplier", 0))) <= 0:
            raise ConfigError(f"profiles.{mode}.atr_multiplier обязан быть > 0")
        if Decimal(str(profile.get("min_trail_r", 0))) <= 0:
            raise ConfigError(f"profiles.{mode}.min_trail_r обязан быть > 0")
        if int(profile.get("time_stop_hours", 0)) <= 0:
            raise ConfigError(f"profiles.{mode}.time_stop_hours обязан быть > 0")

    optimizer = management.get("optimizer", {})
    if int(optimizer.get("cadence_days", 0)) < 1:
        raise ConfigError("risk.management.optimizer.cadence_days должен быть >= 1")
    if int(optimizer.get("min_samples", 0)) < 20:
        raise ConfigError("risk.management.optimizer.min_samples должен быть >= 20")
    candidates = optimizer.get("candidates", [])
    ids: set[str] = set()
    for candidate in candidates:
        cid = str(candidate.get("id", ""))
        if not cid or cid in ids:
            raise ConfigError("optimizer candidate id пуст или повторяется")
        ids.add(cid)
        candidate_profiles = candidate.get("profiles", {})
        if set(candidate_profiles) != required:
            raise ConfigError(f"optimizer.{cid}: нужны все три профиля")
        for mode, profile in candidate_profiles.items():
            # Optimizer принципиально не получает risk_multiplier: иначе exit
            # search незаметно превратится в поиск большего риска.
            if "risk_multiplier" in profile:
                raise ConfigError(f"optimizer.{cid}.{mode}: risk_multiplier запрещён")
            _validate_shares(
                profile.get("tp_shares"),
                label=f"optimizer.{cid}.{mode}.tp_shares",
            )
            giveback = Decimal(str(profile.get("mfe_giveback", -1)))
            if not (Decimal(0) < giveback < Decimal(1)):
                raise ConfigError(f"optimizer.{cid}.{mode}.mfe_giveback вне (0,1)")
    if "baseline" not in ids:
        raise ConfigError("optimizer обязан содержать candidate baseline")


def _validate_rebalance_economics(portfolio: dict[str, Any]) -> None:
    """Validate a generic estimate policy without embedding tax law in code."""

    raw = portfolio.get("rebalance_economics")
    if raw is None:
        return
    if not isinstance(raw, dict):
        raise ConfigError(
            "portfolio.rebalance_economics должен быть словарём"
        )
    policy_id = str(raw.get("policy_id") or "").strip()
    fee_bps = raw.get("fee_bps")
    tax_rate = raw.get("capital_gain_tax_rate")
    if not policy_id and fee_bps is None and tax_rate is None:
        return
    if not policy_id or fee_bps is None or tax_rate is None:
        raise ConfigError(
            "portfolio.rebalance_economics требует policy_id, fee_bps и "
            "capital_gain_tax_rate вместе"
        )
    fee = Decimal(str(fee_bps))
    if not fee.is_finite() or fee < 0:
        raise ConfigError(
            "portfolio.rebalance_economics.fee_bps "
            "не может быть отрицательным"
        )
    rate = Decimal(str(tax_rate))
    if not rate.is_finite() or not (Decimal(0) <= rate <= Decimal(1)):
        raise ConfigError(
            "portfolio.rebalance_economics.capital_gain_tax_rate вне [0,1]"
        )


def _validate(data: dict[str, Any]) -> None:
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

    _validate_risk_management(risk)
    portfolio = data.get("portfolio", {})
    if not isinstance(portfolio, dict):
        raise ConfigError("portfolio должен быть словарём")
    _validate_rebalance_economics(portfolio)


def load_config(path: str | os.PathLike[str] | None = None) -> EngineConfig:
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
    return load_config(path)

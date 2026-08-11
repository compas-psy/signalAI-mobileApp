"""Подписываемая политика долей выхода для server-paper.

Risk Engine v2 сохраняет выбранный профиль прямо в immutable TradeIdea.
Approve копирует его доли в PaperTrade. Поэтому еженедельный optimizer может
улучшить только будущие сделки: уже подтверждённая никогда не меняет exit
policy задним числом.

Для идей старой версии без ``risk_policy`` сохраняется legacy 40/40/20.
"""

from __future__ import annotations

from decimal import Decimal

from ..config import EngineConfig, get_config
from ..models import TradeIdea
from . import tracker


def _normalized(values: list[Decimal], count: int) -> list[Decimal]:
    if count <= 0:
        return []
    if len(values) < count or any(value <= 0 for value in values[:count]):
        raise ValueError("policy shares не покрывают все цели")
    selected = values[:count]
    total = sum(selected, Decimal(0))
    if total <= 0:
        raise ValueError("сумма долей TP должна быть положительной")
    return [value / total for value in selected]


def _configured_shares(count: int, *, cfg: EngineConfig | None = None) -> list[Decimal]:
    config = cfg or get_config()
    raw = config.get("risk.management.tp_shares")
    return _normalized([Decimal(str(value)) for value in raw], count)


def _idea_policy_shares(idea: TradeIdea, count: int) -> list[Decimal] | None:
    policy = (idea.explanation_json or {}).get("risk_policy") or {}
    raw = policy.get("tp_shares")
    if not isinstance(raw, list) or not raw:
        return None
    try:
        values = [Decimal(str(value)) for value in raw]
        return _normalized(values, count)
    except (ArithmeticError, ValueError):
        # Невалидный snapshot не подменяем догадкой: fallback — прежняя
        # подписанная глобальная policy, а не equal thirds.
        return None


def targets_with_policy(idea: TradeIdea) -> tuple[list[Decimal], list[Decimal]]:
    prices = [price for price in (idea.tp1, idea.tp2, idea.tp3) if price is not None]
    shares = _idea_policy_shares(idea, len(prices))
    if shares is None:
        shares = _configured_shares(len(prices))
    return prices, shares


def install() -> None:
    """Подменить legacy equal-share builder до создания новых PaperTrade."""
    if tracker._targets is not targets_with_policy:
        tracker._targets = targets_with_policy


__all__ = ["install", "targets_with_policy"]

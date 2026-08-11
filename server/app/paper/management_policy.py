"""Единая политика долей TP для server-paper.

Клиент подписывает план с 40% / 40% / 20%, а legacy tracker до сих пор делил
любой набор целей поровну. В результате цифра потенциальной прибыли на экране
и фактический realized R сопровождения описывали разные сделки.

Доли хранятся в конфиге и копируются в PaperTrade в момент approve. Поэтому
уже созданная сделка не изменится задним числом при будущей смене политики.
"""

from __future__ import annotations

from decimal import Decimal

from ..config import EngineConfig, get_config
from ..models import TradeIdea
from . import tracker


def _configured_shares(count: int, *, cfg: EngineConfig | None = None) -> list[Decimal]:
    if count <= 0:
        return []
    config = cfg or get_config()
    raw = config.get("risk.management.tp_shares")
    values = [Decimal(str(value)) for value in raw]
    if len(values) < count or any(value <= 0 for value in values[:count]):
        raise ValueError("risk.management.tp_shares не покрывает все цели")

    selected = values[:count]
    total = sum(selected, Decimal(0))
    if total <= 0:
        raise ValueError("сумма долей TP должна быть положительной")
    # Если у конкретной идеи только 1–2 цели, оставшиеся доли не пропадают:
    # выбранные цели получают 100% позиции пропорционально политике.
    return [value / total for value in selected]


def targets_with_policy(idea: TradeIdea) -> tuple[list[Decimal], list[Decimal]]:
    prices = [price for price in (idea.tp1, idea.tp2, idea.tp3) if price is not None]
    return prices, _configured_shares(len(prices))


def install() -> None:
    """Подменить legacy equal-share builder до создания новых PaperTrade."""
    if tracker._targets is not targets_with_policy:
        tracker._targets = targets_with_policy


__all__ = ["install", "targets_with_policy"]

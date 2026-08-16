"""Подписываемая политика и runtime сопровождения server-paper.

Risk Engine v2 сохраняет выбранный профиль прямо в immutable TradeIdea.
Approve копирует его доли в PaperTrade. Поэтому еженедельный optimizer может
улучшить только будущие сделки: уже подтверждённая никогда не меняет exit
policy задним числом.

Этот модуль также устанавливает единый ``advance_v2`` как фактический runtime
для обоих execution sensors: обычного H1 tracker (FORTS) и минутного crypto
tracker. Раньше v2 был реализован, но оба runtime продолжали вызывать legacy
``advance``; TP3-runner, ATR/MFE trail и alpha-decay поэтому существовали в
коде, но не в реально сопровождаемой сделке.

Для идей старой версии без ``risk_policy`` сохраняется legacy 40/40/20.
Сам ``advance_v2`` узнаёт legacy-план по сохранённым долям и оставляет прежнюю
семантику, поэтому деплой не переписывает уже открытые старые сделки.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..config import EngineConfig, get_config
from ..models import TradeIdea
from ..risk.dynamic_exit import advance_v2
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


def signed_policy_for_shares(
    raw_shares: list[Any] | tuple[Any, ...], *, cfg: EngineConfig | None = None
) -> dict[str, Any] | None:
    """Узнать подписанную v2 policy по сохранённым долям PaperTrade.

    Это read-only helper для API/диагностики. Он не выбирает новый профиль и
    не считает риск. Совпадение ищется среди базовых профилей и bounded
    optimizer candidates, чтобы после рестарта/API-запроса можно было честно
    показать владельцу, что TP3 уже стал runner-ом.
    """
    if len(raw_shares) < 3:
        return None
    try:
        values = [Decimal(str(value)) for value in raw_shares]
        normalized = _normalized(values, len(values))
    except (ArithmeticError, ValueError):
        return None

    config = cfg or get_config()
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for mode in ("defensive", "balanced", "conviction"):
        candidates.append(
            ("base", mode, dict(config.get(f"risk.management.profiles.{mode}")))
        )
    for candidate in config.get("risk.management.optimizer.candidates"):
        candidate_id = str(candidate.get("id", ""))
        for mode, profile in (candidate.get("profiles") or {}).items():
            candidates.append((candidate_id, str(mode), dict(profile)))

    for candidate_id, mode, profile in candidates:
        expected_raw = profile.get("tp_shares")
        if not isinstance(expected_raw, list) or len(expected_raw) != len(normalized):
            continue
        expected = _normalized(
            [Decimal(str(value)) for value in expected_raw], len(expected_raw)
        )
        if all(
            abs(left - right) <= Decimal("0.00000001")
            for left, right in zip(expected, normalized, strict=True)
        ):
            base = dict(config.get(f"risk.management.profiles.{mode}"))
            base.update(profile)
            base["mode"] = mode
            base["candidate_id"] = candidate_id
            base["version"] = str(config.get("risk.management.policy_version"))
            return base
    return None


def install() -> None:
    """Установить подписанный plan builder и единый dynamic-exit runtime.

    ``live_tracker`` импортирует ``advance`` по значению, поэтому одной
    подмены ``tracker.advance`` недостаточно: локальную ссылку minute tracker
    тоже обновляем явно. Импорт внутри функции избегает цикла при загрузке
    модулей и делает установку идемпотентной.
    """
    if tracker._targets is not targets_with_policy:
        tracker._targets = targets_with_policy
    if tracker.advance is not advance_v2:
        tracker.advance = advance_v2

    from . import live_tracker

    if live_tracker.advance is not advance_v2:
        live_tracker.advance = advance_v2


__all__ = ["install", "signed_policy_for_shares", "targets_with_policy"]
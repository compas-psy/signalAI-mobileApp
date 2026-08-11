"""Dynamic exit engine shared by FORTS/H1 and crypto/1m paper tracking.

TP3 in RiskPolicy v2 is not a hard ceiling. It activates the runner. The
remaining signed share is closed only by a monotonic volatility/MFE trail,
stop, or alpha-decay time stop. Legacy 40/40/20 trades keep the exact old
behaviour: already signed plans never change retroactively.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from ..config import get_config
from ..models.enums import Direction, PaperStatus


def _d(value: Any) -> Decimal:
    return Decimal(str(value))


def _is_long(trade) -> bool:
    return str(trade.direction) == str(Direction.LONG)


def _risk(trade) -> Decimal:
    value = abs(_d(trade.entry) - _d(trade.initial_stop))
    return value if value > 0 else Decimal(1)


def _r_of(trade, price: Decimal) -> Decimal:
    move = price - _d(trade.entry) if _is_long(trade) else _d(trade.entry) - price
    return move / _risk(trade)


def _price_at_r(trade, value: Decimal) -> Decimal:
    sign = Decimal(1) if _is_long(trade) else Decimal(-1)
    return _d(trade.entry) + sign * _risk(trade) * value


def _profile_for(trade) -> dict[str, Any] | None:
    """Восстановить подписанную policy из долей, не меняя схему БД.

    Новая идея копирует profile shares в PaperTrade в approve-time. Старые
    сделки имеют legacy 40/40/20 и сюда не попадают, поэтому deployment не
    переписывает их сопровождение задним числом.
    """
    shares = [_d(value) for value in (trade.tp_shares or [])]
    if len(shares) < 3:
        return None
    total = sum(shares, Decimal(0))
    if total <= 0:
        return None
    normalized = [value / total for value in shares]
    cfg = get_config()
    for mode in ("defensive", "balanced", "conviction"):
        raw = dict(cfg.get(f"risk.management.profiles.{mode}"))
        expected = [_d(v) for v in raw["tp_shares"]]
        e_total = sum(expected, Decimal(0))
        expected = [value / e_total for value in expected]
        if len(expected) == len(normalized) and all(
            abs(a - b) <= Decimal("0.00000001")
            for a, b in zip(expected, normalized, strict=True)
        ):
            raw["mode"] = mode
            return raw
    return None


def _remaining_share(shares: list[Decimal], taken: int, *, runner: bool) -> Decimal:
    if runner and taken >= len(shares):
        return shares[-1]
    return max(Decimal(0), Decimal(1) - sum(shares[:taken], Decimal(0)))


def _move_stop(trade, target: Decimal) -> bool:
    current = _d(trade.current_stop)
    better = target > current if _is_long(trade) else target < current
    if not better:
        return False
    trade.current_stop = target
    entry = _d(trade.entry)
    protected = target >= entry if _is_long(trade) else target <= entry
    if protected and trade.breakeven_at is None:
        # Конкретный timestamp ставит advance вызывающим после движения.
        return True
    return True


def _close_at(trade, price: Decimal, remaining: Decimal, *, at, outcome: str, reason: str) -> None:
    trade.realized_r += _r_of(trade, price) * remaining
    trade.status = PaperStatus.CLOSED
    trade.closed_at = at
    trade.outcome = outcome
    trade.close_reason = reason


def _legacy_advance(trade, bars: Sequence[Any]) -> list[str]:
    """Буквально прежняя семантика для уже подписанных legacy trades."""
    events: list[str] = []
    prices = [_d(p) for p in trade.tp_prices]
    shares = [_d(s) for s in trade.tp_shares]
    long = _is_long(trade)

    for bar in bars:
        if PaperStatus(trade.status) is PaperStatus.PENDING:
            if not (_d(bar.low) <= _d(trade.entry) <= _d(bar.high)):
                continue
            trade.status = PaperStatus.OPEN
            events.append("вход исполнен")

        if PaperStatus(trade.status) is not PaperStatus.OPEN:
            break

        stop_hit = _d(bar.low) <= _d(trade.current_stop) if long else _d(bar.high) >= _d(trade.current_stop)
        if stop_hit:
            remaining = Decimal(1) - sum(shares[: int(trade.tps_taken)], Decimal(0))
            _close_at(
                trade,
                _d(trade.current_stop),
                remaining,
                at=bar.open_time,
                outcome="BE" if trade.breakeven_at else "SL",
                reason="стоп в безубытке" if trade.breakeven_at else "стоп",
            )
            events.append(trade.close_reason)
            return events

        while int(trade.tps_taken) < len(prices):
            target = prices[int(trade.tps_taken)]
            reached = _d(bar.high) >= target if long else _d(bar.low) <= target
            if not reached:
                break
            index = int(trade.tps_taken)
            trade.realized_r += _r_of(trade, target) * shares[index]
            trade.tps_taken = index + 1
            events.append(f"цель {trade.tps_taken} взята")
            if int(trade.tps_taken) == 1:
                trade.current_stop = trade.entry
                trade.breakeven_at = bar.open_time
                events.append("стоп в безубытке")
            elif int(trade.tps_taken) == 2:
                trade.current_stop = prices[0]
                events.append("стоп подтянут к первой цели")

        if int(trade.tps_taken) >= len(prices):
            trade.status = PaperStatus.CLOSED
            trade.closed_at = bar.open_time
            trade.outcome = "TP"
            trade.close_reason = "все цели взяты"
            events.append(trade.close_reason)
            return events
    return events


def advance_v2(trade, bars: Sequence[Any], *, now) -> list[str]:
    """Единый dynamic exit для всех execution sensors.

    Внутри бара stop по-прежнему проверяется первым. Новый trailing,
    рассчитанный по high/low текущего бара, начинает действовать только со
    следующего бара: использовать его задним числом внутри той же свечи было
    бы look-ahead bias.
    """
    policy = _profile_for(trade)
    if policy is None:
        return _legacy_advance(trade, bars)

    events: list[str] = []
    prices = [_d(p) for p in trade.tp_prices]
    shares = [_d(s) for s in trade.tp_shares]
    long = _is_long(trade)
    runner_enabled = bool(policy.get("runner_enabled", True)) and len(prices) >= 3
    runner_index = len(prices) - 1
    tr_values: list[Decimal] = []
    previous_close: Decimal | None = None
    max_favorable_r = Decimal(0)

    for bar in bars:
        high, low, close = _d(bar.high), _d(bar.low), _d(bar.close)
        if previous_close is None:
            true_range = high - low
        else:
            true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
        previous_close = close
        tr_values.append(true_range)
        del tr_values[:-14]

        if PaperStatus(trade.status) is PaperStatus.PENDING:
            if not (low <= _d(trade.entry) <= high):
                continue
            trade.status = PaperStatus.OPEN
            # Для v2 opened_at становится временем фактического fill. До fill
            # оно остаётся approval-time и продолжает защищать от replay.
            trade.opened_at = bar.open_time
            events.append("вход исполнен")

        if PaperStatus(trade.status) is not PaperStatus.OPEN:
            break

        # Сначала действующий на начало бара stop — консервативная chronology.
        stop_hit = low <= _d(trade.current_stop) if long else high >= _d(trade.current_stop)
        if stop_hit:
            remaining = _remaining_share(shares, int(trade.tps_taken), runner=runner_enabled)
            _close_at(
                trade,
                _d(trade.current_stop),
                remaining,
                at=bar.open_time,
                outcome="BE" if trade.breakeven_at else "SL",
                reason="динамический стоп" if trade.breakeven_at else "стоп",
            )
            events.append(trade.close_reason)
            return events

        # Partial exits. Последняя цель становится activation runner-а и не
        # фиксирует последнюю долю позиции.
        while int(trade.tps_taken) < len(prices):
            index = int(trade.tps_taken)
            target = prices[index]
            reached = high >= target if long else low <= target
            if not reached:
                break
            if runner_enabled and index == runner_index:
                trade.tps_taken = index + 1
                events.append("TP3 достигнут — runner активирован")
                break

            trade.realized_r += _r_of(trade, target) * shares[index]
            trade.tps_taken = index + 1
            events.append(f"цель {trade.tps_taken} взята")

            lock_key = "tp1_stop_lock_r" if int(trade.tps_taken) == 1 else "tp2_stop_lock_r"
            if int(trade.tps_taken) in (1, 2):
                lock_r = _d(policy[lock_key])
                moved = _move_stop(trade, _price_at_r(trade, lock_r))
                if moved:
                    entry = _d(trade.entry)
                    protected = (
                        _d(trade.current_stop) >= entry
                        if long
                        else _d(trade.current_stop) <= entry
                    )
                    if protected and trade.breakeven_at is None:
                        trade.breakeven_at = bar.open_time
                    events.append(f"risk-release stop → {lock_r}R")

        if int(trade.tps_taken) >= len(prices) and not runner_enabled:
            trade.status = PaperStatus.CLOSED
            trade.closed_at = bar.open_time
            trade.outcome = "TP"
            trade.close_reason = "все цели взяты"
            events.append(trade.close_reason)
            return events

        favorable_price = high if long else low
        max_favorable_r = max(max_favorable_r, _r_of(trade, favorable_price))

        runner_active = runner_enabled and int(trade.tps_taken) >= len(prices)
        if runner_active:
            atr = sum(tr_values, Decimal(0)) / Decimal(len(tr_values))
            atr_r = atr / _risk(trade)
            trail_distance_r = max(
                _d(policy["min_trail_r"]),
                _d(policy["atr_multiplier"]) * atr_r,
            )
            atr_lock = max_favorable_r - trail_distance_r
            giveback_lock = max_favorable_r * (Decimal(1) - _d(policy["mfe_giveback"]))
            structural_floor = _d(policy["tp2_stop_lock_r"])
            lock_r = max(structural_floor, atr_lock, giveback_lock)
            if _move_stop(trade, _price_at_r(trade, lock_r)):
                if trade.breakeven_at is None:
                    trade.breakeven_at = bar.open_time
                events.append(f"runner trail → {lock_r.quantize(Decimal('0.01'))}R")

        # Alpha decay: если после фактического fill позиция долго не дошла даже
        # до TP1, освобождаем risk budget по закрытию бара вместо ожидания
        # полного SL. Runner/частично реализованные позиции time-stop не режет.
        if int(trade.tps_taken) == 0:
            opened = trade.opened_at
            age_hours = (bar.open_time - opened).total_seconds() / 3600
            if age_hours >= int(policy["time_stop_hours"]):
                remaining = Decimal(1)
                _close_at(
                    trade,
                    close,
                    remaining,
                    at=bar.open_time,
                    outcome="TIME",
                    reason="alpha-decay time stop",
                )
                events.append(trade.close_reason)
                return events

    return events


__all__ = ["advance_v2"]

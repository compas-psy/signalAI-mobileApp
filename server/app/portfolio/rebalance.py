"""Черновик ребаланса от фактических позиций (engine-ТЗ §6.6, UX-ТЗ §7.2).

Именно черновик и именно предложение. Приложение не отправляет по нему ни
одной заявки: инвестиционный контур читает счёт токеном на чтение, считает
пакеты и говорит, что стоило бы поправить, — сделки владелец совершает сам.
Это не ограничение возможностей, а условие задачи: за инвестиционным счётом
стоит основной капитал, и распоряжаться им приложение права не имеет.

Из чего считается расхождение. Целевой состав — доли пакета; фактический —
рыночная стоимость позиций на счёте. Сравниваются **доли**, а не суммы:
портфель, выросший на 20%, не разбалансирован, а portfolio, где акции
выросли, а облигации нет, — разбалансирован при той же общей сумме.

Чего здесь нет и быть не должно: предложения «доложить денег». Ребаланс
работает с тем, что есть на счёте, и приводит доли к целевым продажей одного
и покупкой другого. Совет «внесите ещё» — это не ребаланс.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import EngineConfig, get_config
from ..models import Holding, PortfolioModel, RebalanceDraft
from .lifecycle import ModelDiff, model_diff

# Ниже этого расхождения доли ребаланс не предлагается.
#
# Порог — не вкусовщина, а защита от того, чтобы советовать сделку, которая
# не окупит комиссии. Доля, уехавшая на полпроцента, вернётся сама на
# следующем движении рынка, а сделка ради неё стоит денег и внимания.
DRIFT_THRESHOLD = Decimal("0.03")

# Расхождение, при котором ребаланс перестаёт быть «можно» и становится
# «пора»: четверть целевой доли для крупной позиции — это уже другой портфель.
URGENT_DRIFT = Decimal("0.08")

_RUB_QUANTUM = Decimal("0.01")
_BPS_DIVISOR = Decimal("10000")


class EconomicsStatus(StrEnum):
    """How much of a rebalance action's economics can actually be proven."""

    UNKNOWN = "UNKNOWN"
    ESTIMATED = "ESTIMATED"
    BROKER_FINAL = "BROKER_FINAL"


@dataclass(frozen=True, slots=True)
class RebalanceEconomicsPolicy:
    """Configurable calculation inputs, never a statement of tax law.

    The configured rate merely describes the owner-approved estimate policy for
    one account/broker setup.  If it is absent or incomplete, the result stays
    ``UNKNOWN`` instead of pretending that tax or fees are zero.
    """

    policy_id: str
    fee_bps: Decimal
    capital_gain_tax_rate: Decimal

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise ValueError("economics policy_id is required")
        if not self.fee_bps.is_finite() or self.fee_bps < 0:
            raise ValueError("economics fee_bps must be finite and non-negative")
        if not self.capital_gain_tax_rate.is_finite() or not (
            Decimal(0) <= self.capital_gain_tax_rate <= Decimal(1)
        ):
            raise ValueError("economics capital_gain_tax_rate must be in [0, 1]")


def economics_policy_from_config(
    config: EngineConfig | None = None,
) -> RebalanceEconomicsPolicy | None:
    """Return a complete policy only; incomplete config is fail-closed.

    Existing deployments have no approved broker fee/tax inputs.  Returning
    ``None`` intentionally keeps their drafts non-actionable until an owner
    supplies all three values through configuration.
    """

    cfg = config or get_config()
    raw = cfg.section("portfolio").get("rebalance_economics")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("portfolio.rebalance_economics must be a mapping")
    policy_id = str(raw.get("policy_id") or "").strip()
    fee_bps = raw.get("fee_bps")
    tax_rate = raw.get("capital_gain_tax_rate")
    if not policy_id and fee_bps is None and tax_rate is None:
        return None
    if not policy_id or fee_bps is None or tax_rate is None:
        return None
    return RebalanceEconomicsPolicy(
        policy_id=policy_id,
        fee_bps=Decimal(str(fee_bps)),
        capital_gain_tax_rate=Decimal(str(tax_rate)),
    )


@dataclass(frozen=True, slots=True)
class ActionEconomics:
    """Estimate/final economics with inputs preserved for owner review."""

    status: EconomicsStatus = EconomicsStatus.UNKNOWN
    actionable: bool = False
    order_quantity: Decimal | None = None
    order_notional_rub: Decimal | None = None
    estimated_costs_rub: Decimal | None = None
    estimated_tax_rub: Decimal | None = None
    broker_final_costs_rub: Decimal | None = None
    broker_final_tax_rub: Decimal | None = None
    provenance: dict[str, str] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "status": self.status.value,
            "actionable": self.actionable,
            "order_quantity": _decimal_text(self.order_quantity),
            "order_notional_rub": _decimal_text(self.order_notional_rub),
            "estimated_costs_rub": _decimal_text(self.estimated_costs_rub),
            "estimated_tax_rub": _decimal_text(self.estimated_tax_rub),
            "broker_final_costs_rub": _decimal_text(self.broker_final_costs_rub),
            "broker_final_tax_rub": _decimal_text(self.broker_final_tax_rub),
            "provenance": dict(self.provenance),
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True, slots=True)
class BrokerFinalEconomics:
    """Actual broker settlement values, intentionally separate from estimates."""

    costs_rub: Decimal
    tax_rub: Decimal
    reference: str
    executed_quantity: Decimal | None = None
    executed_notional_rub: Decimal | None = None

    def __post_init__(self) -> None:
        if self.costs_rub < 0 or self.tax_rub < 0:
            raise ValueError("broker final economics must be non-negative")
        if not self.reference.strip():
            raise ValueError("broker final economics reference is required")
        for field_name, value in (
            ("executed_quantity", self.executed_quantity),
            ("executed_notional_rub", self.executed_notional_rub),
        ):
            if value is not None and value <= 0:
                raise ValueError(f"broker final {field_name} must be positive")


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


@dataclass(frozen=True, slots=True)
class Action:
    """Одно действие черновика."""

    instrument_id: str
    side: str  # BUY / SELL
    target_weight: Decimal
    actual_weight: Decimal
    amount_rub: Decimal
    reason: str
    economics: ActionEconomics = field(default_factory=ActionEconomics)

    def as_dict(self) -> dict:
        return {
            "instrument_id": self.instrument_id,
            "side": self.side,
            "target_weight": str(self.target_weight),
            "actual_weight": str(self.actual_weight),
            "amount_rub": str(self.amount_rub),
            "reason": self.reason,
            "economics": self.economics.as_dict(),
        }


@dataclass
class Draft:
    """Черновик целиком: что делать и почему."""

    model_id: object | None = None
    total_value: Decimal = Decimal(0)
    actions: list[Action] = field(default_factory=list)
    max_drift: Decimal = Decimal(0)
    reason: str = ""
    urgent: bool = False
    model_changed: bool = False

    @property
    def needed(self) -> bool:
        return bool(self.actions)

    @property
    def actionable(self) -> bool:
        return self.needed and all(action.economics.actionable for action in self.actions)

    @property
    def economics_status(self) -> EconomicsStatus:
        if not self.needed or any(
            action.economics.status is EconomicsStatus.UNKNOWN for action in self.actions
        ):
            return EconomicsStatus.UNKNOWN
        if all(
            action.economics.status is EconomicsStatus.BROKER_FINAL
            for action in self.actions
        ):
            return EconomicsStatus.BROKER_FINAL
        return EconomicsStatus.ESTIMATED

    @property
    def estimated_costs_rub(self) -> Decimal | None:
        if not self.actionable or any(
            action.economics.estimated_costs_rub is None for action in self.actions
        ):
            return None
        return sum(
            (action.economics.estimated_costs_rub for action in self.actions),
            Decimal(0),
        ).quantize(_RUB_QUANTUM, rounding=ROUND_HALF_UP)

    @property
    def estimated_tax_rub(self) -> Decimal | None:
        if not self.actionable or any(
            action.economics.estimated_tax_rub is None for action in self.actions
        ):
            return None
        return sum(
            (action.economics.estimated_tax_rub for action in self.actions),
            Decimal(0),
        ).quantize(_RUB_QUANTUM, rounding=ROUND_HALF_UP)

    @property
    def broker_final_costs_rub(self) -> Decimal | None:
        if self.economics_status is not EconomicsStatus.BROKER_FINAL:
            return None
        return sum(
            (
                action.economics.broker_final_costs_rub or Decimal(0)
                for action in self.actions
            ),
            Decimal(0),
        ).quantize(_RUB_QUANTUM, rounding=ROUND_HALF_UP)

    @property
    def broker_final_tax_rub(self) -> Decimal | None:
        if self.economics_status is not EconomicsStatus.BROKER_FINAL:
            return None
        return sum(
            (
                action.economics.broker_final_tax_rub or Decimal(0)
                for action in self.actions
            ),
            Decimal(0),
        ).quantize(_RUB_QUANTUM, rounding=ROUND_HALF_UP)

    @property
    def economics_provenance(self) -> dict:
        policy_ids = {
            action.economics.provenance.get("policy_id", "")
            for action in self.actions
            if action.economics.provenance.get("policy_id")
        }
        return {
            "status": self.economics_status.value,
            "policy_id": next(iter(policy_ids)) if len(policy_ids) == 1 else "",
            "actions": {
                action.instrument_id: action.economics.as_dict()
                for action in self.actions
            },
        }


def _weights(holdings: list[Holding]) -> tuple[Decimal, dict[str, Decimal]]:
    """Фактические доли по рыночной стоимости."""
    total = sum((h.market_value or Decimal(0)) for h in holdings)
    if total <= 0:
        return Decimal(0), {}
    return total, {
        h.instrument_id: (h.market_value or Decimal(0)) / total for h in holdings
    }


def _unknown_economics(
    *blockers: str,
    provenance: Mapping[str, str] | None = None,
) -> ActionEconomics:
    return ActionEconomics(
        provenance=dict(provenance or {}),
        blockers=tuple(sorted(set(blockers))),
    )


def _estimate_action_economics(
    action: Action,
    *,
    holding: Holding | None,
    policy: RebalanceEconomicsPolicy | None,
    lot_size: int | None,
) -> ActionEconomics:
    """Estimate one manual order only when every material input is known."""

    if policy is None:
        return _unknown_economics("fee_policy")

    provenance: dict[str, str] = {
        "policy_id": policy.policy_id,
        "fee_bps": str(policy.fee_bps),
        "capital_gain_tax_rate": str(policy.capital_gain_tax_rate),
    }
    blockers: list[str] = []
    if holding is None:
        blockers.extend(("holding_snapshot", "market_price"))
    else:
        if holding.market_price is None or holding.market_price <= 0:
            blockers.append("market_price")
        else:
            provenance["quote_source"] = "holding_snapshot.market_price"
            provenance["market_price"] = str(holding.market_price)
        if action.side == "SELL":
            if holding.quantity is None or holding.quantity <= 0:
                blockers.append("holding_quantity")
            else:
                provenance["holding_quantity"] = str(holding.quantity)
            if holding.average_price is None or holding.average_price < 0:
                blockers.append("cost_basis")
            else:
                provenance["cost_basis_source"] = "holding_snapshot.average_price"
                provenance["average_price"] = str(holding.average_price)
    if lot_size is None or lot_size <= 0:
        blockers.append("lot_size")
    else:
        provenance["lot_size"] = str(lot_size)

    if blockers:
        return _unknown_economics(*blockers, provenance=provenance)

    assert holding is not None
    assert holding.market_price is not None
    assert lot_size is not None
    lot_notional = holding.market_price * Decimal(lot_size)
    lots = (action.amount_rub / lot_notional).quantize(Decimal(1), rounding=ROUND_DOWN)
    quantity = lots * Decimal(lot_size)
    if quantity <= 0:
        return _unknown_economics("minimum_lot", provenance=provenance)
    if action.side == "SELL" and quantity > holding.quantity:
        return _unknown_economics(
            "holding_quantity",
            "holding_snapshot_inconsistent",
            provenance=provenance,
        )

    notional = (quantity * holding.market_price).quantize(
        _RUB_QUANTUM, rounding=ROUND_HALF_UP
    )
    fee = (notional * policy.fee_bps / _BPS_DIVISOR).quantize(
        _RUB_QUANTUM, rounding=ROUND_HALF_UP
    )
    tax = Decimal(0)
    if action.side == "SELL":
        assert holding.average_price is not None
        taxable_gain = (
            max(Decimal(0), holding.market_price - holding.average_price) * quantity
        )
        tax = (taxable_gain * policy.capital_gain_tax_rate).quantize(
            _RUB_QUANTUM, rounding=ROUND_HALF_UP
        )
    return ActionEconomics(
        status=EconomicsStatus.ESTIMATED,
        actionable=True,
        order_quantity=quantity,
        order_notional_rub=notional,
        estimated_costs_rub=fee,
        estimated_tax_rub=tax,
        provenance=provenance,
    )


def _with_broker_finals(
    draft: Draft,
    broker_finals: Mapping[str, BrokerFinalEconomics] | None,
) -> Draft:
    if not broker_finals:
        return draft
    unknown_ids = set(broker_finals) - {action.instrument_id for action in draft.actions}
    if unknown_ids:
        raise ValueError("broker final economics contain unknown rebalance action")

    def final_matches(action: Action, final: BrokerFinalEconomics) -> bool:
        economics = action.economics
        return (
            economics.status is EconomicsStatus.ESTIMATED
            and economics.actionable
            and not economics.blockers
            and economics.order_quantity is not None
            and economics.order_notional_rub is not None
            and final.executed_quantity is not None
            and final.executed_notional_rub is not None
            and final.executed_quantity == economics.order_quantity
            and final.executed_notional_rub == economics.order_notional_rub
        )

    draft.actions = [
        replace(
            action,
            economics=(
                replace(
                    action.economics,
                    status=EconomicsStatus.BROKER_FINAL,
                    actionable=action.economics.actionable,
                    broker_final_costs_rub=broker_finals[action.instrument_id].costs_rub,
                    broker_final_tax_rub=broker_finals[action.instrument_id].tax_rub,
                    provenance={
                        **action.economics.provenance,
                        "broker_final_reference": broker_finals[
                            action.instrument_id
                        ].reference,
                        "broker_final_executed_quantity": str(
                            broker_finals[action.instrument_id].executed_quantity
                        ),
                        "broker_final_executed_notional_rub": str(
                            broker_finals[action.instrument_id].executed_notional_rub
                        ),
                    },
                )
                if action.instrument_id in broker_finals
                and final_matches(action, broker_finals[action.instrument_id])
                else action.economics
            ),
        )
        for action in draft.actions
    ]
    return draft


def plan(
    model: PortfolioModel,
    holdings: list[Holding],
    *,
    threshold: Decimal = DRIFT_THRESHOLD,
    previous_model: PortfolioModel | None = None,
    economics_policy: RebalanceEconomicsPolicy | None = None,
    lot_sizes: Mapping[str, int] | None = None,
) -> Draft:
    """Сравнить фактический состав с целевым и предложить действия.

    Позиции, которых нет в целевом составе, попадают в продажу целиком —
    но только если их доля выше порога. Бумага на 0,4% счёта, оставшаяся от
    прошлого пакета, не стоит заявки.

    Если передано предыдущее поколение модели, причина каждого действия
    объясняется изменением самой рекомендации там, где оно действительно
    произошло. Без предыдущего поколения сохраняется прежняя логика drift —
    исторический model_id не превращается задним числом в «новую модель».
    """
    draft = Draft(model_id=model.id)
    total, actual = _weights(holdings)
    if total <= 0:
        draft.reason = (
            "на счёте нет позиций: ребалансировать нечего. Пакет — это "
            "предложение, с чего начать"
        )
        return draft
    draft.total_value = total

    target = {w.instrument_id: Decimal(str(w.target_weight)) for w in model.weights}
    holdings_by_instrument = {holding.instrument_id: holding for holding in holdings}
    before_target = (
        {w.instrument_id: Decimal(str(w.target_weight)) for w in previous_model.weights}
        if previous_model is not None
        else {}
    )
    diff = model_diff(previous_model, model) if previous_model is not None else ModelDiff()
    draft.model_changed = diff.material
    universe = sorted(set(target) | set(actual))

    for instrument_id in universe:
        want = target.get(instrument_id, Decimal(0))
        have = actual.get(instrument_id, Decimal(0))
        drift = want - have
        size = abs(drift)
        if size > draft.max_drift:
            draft.max_drift = size
        if size < threshold:
            continue
        amount = (size * total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if instrument_id in diff.removed:
            reason = "удалена из новой модели"
        elif instrument_id in diff.added:
            reason = "добавлена в новую модель"
        elif instrument_id in diff.weight_changed:
            reason = (
                "целевая доля модели изменилась "
                f"{before_target[instrument_id]:.1%} → {want:.1%}"
            )
        elif want == 0:
            reason = "бумаги нет в целевом составе — закрыть позицию"
        elif have == 0:
            reason = f"позиции нет, целевая доля {want:.1%}"
        else:
            reason = f"доля {have:.1%} против целевой {want:.1%}"
        draft.actions.append(
            Action(
                instrument_id=instrument_id,
                side="BUY" if drift > 0 else "SELL",
                target_weight=want,
                actual_weight=have,
                amount_rub=amount,
                reason=reason,
            )
        )

    # Сначала продажи: покупать не на что, пока деньги в другой бумаге.
    draft.actions.sort(key=lambda a: (a.side != "SELL", -a.amount_rub))
    draft.actions = [
        replace(
            action,
            economics=_estimate_action_economics(
                action,
                holding=holdings_by_instrument.get(action.instrument_id),
                policy=economics_policy,
                lot_size=(lot_sizes or {}).get(action.instrument_id),
            ),
        )
        for action in draft.actions
    ]
    draft.urgent = draft.max_drift >= URGENT_DRIFT
    if diff.material:
        draft.reason = (
            f"модель изменилась: +{len(diff.added)} / -{len(diff.removed)} / "
            f"Δ{len(diff.weight_changed)}"
        )
    elif not draft.actions:
        draft.reason = (
            f"состав в пределах допуска: наибольшее расхождение "
            f"{draft.max_drift:.1%} при пороге {threshold:.0%}"
        )
    else:
        draft.reason = (
            f"расхождение до {draft.max_drift:.1%}: "
            f"{len(draft.actions)} действий на "
            f"{sum(a.amount_rub for a in draft.actions):.0f} ₽"
        )
    return draft


def latest_holdings(session: Session, account_id) -> list[Holding]:
    """Позиции счёта на самую свежую дату снимка.

    Снимки хранятся историей, и смешивать даты нельзя: доля, посчитанная по
    вчерашней стоимости одной бумаги и сегодняшней другой, — это не состав
    портфеля ни на один момент времени.
    """
    as_of: date | None = session.execute(
        select(Holding.as_of)
        .where(Holding.account_id == account_id)
        .order_by(Holding.as_of.desc())
        .limit(1)
    ).scalar_one_or_none()
    if as_of is None:
        return []
    return list(
        session.execute(
            select(Holding).where(
                Holding.account_id == account_id, Holding.as_of == as_of
            )
        ).scalars()
    )


def record(
    session: Session,
    model: PortfolioModel,
    draft: Draft,
    *,
    now: datetime | None = None,
    broker_finals: Mapping[str, BrokerFinalEconomics] | None = None,
) -> RebalanceDraft | None:
    """Сохранить черновик. Возвращает None, если делать нечего.

    Черновик без действий не пишется: журнал ребалансов — это история того,
    что предлагалось, и «сегодня ничего» в ней не событие.
    """
    if not draft.needed:
        return None
    draft = _with_broker_finals(draft, broker_finals)
    moment = now or datetime.now(UTC)
    trigger = (
        "model_change"
        if draft.model_changed
        else ("drift_urgent" if draft.urgent else "drift")
    )
    row = RebalanceDraft(
        model_id=model.id,
        trigger_reason=trigger[:64],
        actions_json=[a.as_dict() for a in draft.actions],
        before_json={
            a.instrument_id: str(a.actual_weight) for a in draft.actions
        },
        after_json={a.instrument_id: str(a.target_weight) for a in draft.actions},
        estimated_costs=draft.estimated_costs_rub,
        estimated_tax=draft.estimated_tax_rub,
        tax_is_estimate=draft.economics_status is EconomicsStatus.ESTIMATED,
        economics_status=draft.economics_status.value,
        economics_provenance_json=draft.economics_provenance,
        broker_final_costs=draft.broker_final_costs_rub,
        broker_final_tax=draft.broker_final_tax_rub,
        created_at=moment,
    )
    session.add(row)
    session.flush()
    return row


__all__ = [
    "Action",
    "ActionEconomics",
    "BrokerFinalEconomics",
    "DRIFT_THRESHOLD",
    "Draft",
    "EconomicsStatus",
    "RebalanceEconomicsPolicy",
    "URGENT_DRIFT",
    "economics_policy_from_config",
    "latest_holdings",
    "plan",
    "record",
]

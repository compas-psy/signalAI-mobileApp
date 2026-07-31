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

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Holding, PortfolioModel, RebalanceDraft

# Ниже этого расхождения доли ребаланс не предлагается.
#
# Порог — не вкусовщина, а защита от того, чтобы советовать сделку, которая
# не окупит комиссии. Доля, уехавшая на полпроцента, вернётся сама на
# следующем движении рынка, а сделка ради неё стоит денег и внимания.
DRIFT_THRESHOLD = Decimal("0.03")

# Расхождение, при котором ребаланс перестаёт быть «можно» и становится
# «пора»: четверть целевой доли для крупной позиции — это уже другой портфель.
URGENT_DRIFT = Decimal("0.08")


@dataclass(frozen=True, slots=True)
class Action:
    """Одно действие черновика."""

    instrument_id: str
    side: str  # BUY / SELL
    target_weight: Decimal
    actual_weight: Decimal
    amount_rub: Decimal
    reason: str

    def as_dict(self) -> dict:
        return {
            "instrument_id": self.instrument_id,
            "side": self.side,
            "target_weight": str(self.target_weight),
            "actual_weight": str(self.actual_weight),
            "amount_rub": str(self.amount_rub),
            "reason": self.reason,
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

    @property
    def needed(self) -> bool:
        return bool(self.actions)


def _weights(holdings: list[Holding]) -> tuple[Decimal, dict[str, Decimal]]:
    """Фактические доли по рыночной стоимости."""
    total = sum((h.market_value or Decimal(0)) for h in holdings)
    if total <= 0:
        return Decimal(0), {}
    return total, {
        h.instrument_id: (h.market_value or Decimal(0)) / total for h in holdings
    }


def plan(
    model: PortfolioModel,
    holdings: list[Holding],
    *,
    threshold: Decimal = DRIFT_THRESHOLD,
) -> Draft:
    """Сравнить фактический состав с целевым и предложить действия.

    Позиции, которых нет в целевом составе, попадают в продажу целиком —
    но только если их доля выше порога. Бумага на 0,4% счёта, оставшаяся от
    прошлого пакета, не стоит заявки.
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
        if want == 0:
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
    draft.urgent = draft.max_drift >= URGENT_DRIFT
    if not draft.actions:
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
    session: Session, model: PortfolioModel, draft: Draft, *, now: datetime | None = None
) -> RebalanceDraft | None:
    """Сохранить черновик. Возвращает None, если делать нечего.

    Черновик без действий не пишется: журнал ребалансов — это история того,
    что предлагалось, и «сегодня ничего» в ней не событие.
    """
    if not draft.needed:
        return None
    moment = now or datetime.now(UTC)
    row = RebalanceDraft(
        model_id=model.id,
        trigger_reason=("drift_urgent" if draft.urgent else "drift")[:64],
        actions_json=[a.as_dict() for a in draft.actions],
        before_json={
            a.instrument_id: str(a.actual_weight) for a in draft.actions
        },
        after_json={a.instrument_id: str(a.target_weight) for a in draft.actions},
        # Издержки и налог не выдумываются: считать их нечем, пока не
        # известны комиссия брокера и цена покупки каждого лота. Ноль здесь
        # означает «не посчитано», и признак оценочности стоит рядом.
        estimated_costs=Decimal(0),
        tax_is_estimate=True,
        created_at=moment,
    )
    session.add(row)
    session.flush()
    return row


__all__ = [
    "Action",
    "DRIFT_THRESHOLD",
    "Draft",
    "URGENT_DRIFT",
    "latest_holdings",
    "plan",
    "record",
]

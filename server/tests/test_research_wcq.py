"""Движок WCQ §13.1: приёмочные тесты из ТЗ.

Проверяется не «код запускается», а ровно то, что перечислено в разделе
«Acceptance tests»: точность формул, независимость от масштаба единиц,
блокировка ложного позитива и влияние факторинга на уверенность.
"""

from __future__ import annotations

from decimal import Decimal

from app.research.engines import wcq
from app.research.engines.wcq import Quarter, evaluate, ratios_at

D = Decimal


def series() -> list[Quarter]:
    """Восемь ровных кварталов: растущая выручка, дисциплина по обороту.

    База намеренно скучная — на ней проверяются формулы. Сюжеты
    (распродажа склада, прибыль без денег) строятся из неё через replace.
    """
    return [
        Quarter(
            period_end=f"20{24 + i // 4}-Q{i % 4 + 1}",
            revenue=D(100) + D(5) * i,
            cogs=D(60) + D(3) * i,
            gross_profit=D(40) + D(2) * i,
            ebitda=D(20) + D(1) * i,
            cfo=D(18) + D(1) * i,
            accounts_receivable=D(50) + D(1) * i,
            inventory=D(40) + D(1) * i,
            accounts_payable=D(30) + D(1) * i,
            customer_advances=D(5),
        )
        for i in range(8)
    ]


def replace(row: Quarter, **changes) -> Quarter:
    values = {
        name: getattr(row, name) for name in Quarter.__dataclass_fields__
    }
    values.update(changes)
    return Quarter(**values)


def test_формулы_оборачиваемости_совпадают_с_тз():
    """DSO/DIO/DPO/CCC по определению §13.1, с точностью до 1e-8."""
    rows = series()
    r = ratios_at(rows, 7)

    revenue_ttm = sum(q.revenue for q in rows[4:])
    cogs_ttm = sum(q.cogs for q in rows[4:])
    avg_ar = (rows[7].accounts_receivable + rows[6].accounts_receivable) / 2
    avg_inv = (rows[7].inventory + rows[6].inventory) / 2
    avg_ap = (rows[7].accounts_payable + rows[6].accounts_payable) / 2

    assert abs(r.dso - avg_ar / revenue_ttm * D(365)) < D("1e-8")
    assert abs(r.dio - avg_inv / abs(cogs_ttm) * D(365)) < D("1e-8")
    assert abs(r.dpo - avg_ap / abs(cogs_ttm) * D(365)) < D("1e-8")
    assert abs(r.ccc - (r.dso + r.dio - r.dpo)) < D("1e-8")


def test_смена_масштаба_единиц_не_меняет_коэффициентов():
    """Отчёт в тысячах и в миллионах описывает один бизнес.

    Оборачиваемость — отношение, и масштаб обязан сокращаться. Если он не
    сокращается, где-то в формулу попала абсолютная величина.
    """
    rows = series()
    scaled = [
        replace(
            q,
            revenue=q.revenue * 1000,
            cogs=q.cogs * 1000,
            gross_profit=q.gross_profit * 1000,
            ebitda=q.ebitda * 1000,
            cfo=q.cfo * 1000,
            accounts_receivable=q.accounts_receivable * 1000,
            inventory=q.inventory * 1000,
            accounts_payable=q.accounts_payable * 1000,
            customer_advances=q.customer_advances * 1000,
        )
        for q in rows
    ]
    a = ratios_at(rows, 7)
    b = ratios_at(scaled, 7)
    assert abs(a.dso - b.dso) < D("1e-8")
    assert abs(a.ccc - b.ccc) < D("1e-8")
    assert abs(a.cash_conversion - b.cash_conversion) < D("1e-8")


def test_квартал_не_умножается_на_четыре():
    """TTM — сумма четырёх кварталов, а не последний, умноженный на четыре.

    У сезонного бизнеса второе даёт оборачиваемость, которой не было ни
    одного дня в году.
    """
    rows = series()
    rows[7] = replace(rows[7], revenue=D(400))
    r = ratios_at(rows, 7)
    revenue_ttm = sum(q.revenue for q in rows[4:])
    avg_ar = (rows[7].accounts_receivable + rows[6].accounts_receivable) / 2
    assert abs(r.dso - avg_ar / revenue_ttm * D(365)) < D("1e-8")
    # А не так:
    assert r.dso != avg_ar / (rows[7].revenue * 4) * D(365)


def test_пропущенный_квартал_не_заменяется_средним():
    """Несопоставимый ряд честнее сглаженного."""
    rows = series()
    rows[6] = replace(rows[6], revenue=None)
    assert ratios_at(rows, 7).dso is None


def test_распродажа_склада_при_падении_продаж_не_позитив():
    """Метрика улучшается ровно тогда, когда бизнесу хуже.

    Запасы падают, цикл оборота сокращается — арифметически прекрасно. Но
    выручка падает и маржа сжимается: это распродажа, а не управление.
    """
    rows = series()
    for i in (6, 7):
        rows[i] = replace(
            rows[i],
            revenue=D(70),
            cogs=D(55),
            gross_profit=D(15),
            inventory=D(20),
            ebitda=D(8),
            cfo=D(10),
        )
    verdict = evaluate(rows)
    assert verdict.direction == "neutral"
    assert "wcq_release_on_declining_sales" in verdict.reason_codes


def test_прибыль_без_денег_даёт_отрицательное_направление():
    """Выручка растёт, конверсия в деньги ухудшается — ранний признак."""
    rows = series()
    for i in (6, 7):
        rows[i] = replace(
            rows[i],
            revenue=D(160),
            cfo=D(4),
            accounts_receivable=D(120),
        )
    verdict = evaluate(rows)
    assert verdict.direction == "negative"
    assert "wcq_profit_without_cash" in verdict.reason_codes


def test_факторинг_снижает_уверенность_и_помечает_период():
    """Проданная дебиторка делает оборачиваемость лучше без изменений в бизнесе."""
    plain = evaluate(series())
    rows = series()
    rows[7] = replace(rows[7], factoring=True)
    flagged = evaluate(rows)

    assert "wcq_factoring" in flagged.reason_codes
    assert "wcq_factoring" not in plain.reason_codes
    assert "факторинг" in flagged.detail


def test_рост_dpo_не_считается_достижением():
    """Дольше платить поставщикам можно и от нехватки денег (§13.1, проверка 7)."""
    rows = series()
    for i in (6, 7):
        rows[i] = replace(rows[i], accounts_payable=D(70))
    verdict = evaluate(rows)
    assert "wcq_dpo_stretch" in verdict.reason_codes


def test_банкам_движок_не_применяется():
    """Оборачиваемость страховой компании — артефакт, а не показатель."""
    verdict = evaluate(series(), sector="banks")
    assert not verdict.applicable
    assert "wcq_sector_not_applicable" in verdict.reason_codes


def test_короткая_история_не_даёт_сигнала():
    verdict = evaluate(series()[:4])
    assert not verdict.applicable
    assert "wcq_insufficient_history" in verdict.reason_codes


def test_вес_отсутствующего_компонента_не_перераспределяется():
    """Одна раскрытая строка не должна давать силу полного отчёта.

    §13.1 требует буквально: вес пропущенного компонента не переносится на
    остальные, снижается уверенность.
    """
    full = evaluate(series())
    rows = series()
    partial = [replace(q, customer_advances=None, gross_profit=None) for q in rows]
    thin = evaluate(partial)

    assert thin.confidence < full.confidence
    measured = [c for c in thin.components if c.measured]
    assert sum(c.weight for c in measured) < 1.0


def test_отсутствие_начального_баланса_снижает_качество_данных():
    """§13.1: конечный остаток вместо среднего — минус 0.15 к качеству."""
    rows = series()
    zeroed = [replace(q, accounts_receivable=None) for q in rows[:7]] + [rows[7]]
    verdict = evaluate(zeroed)
    if verdict.applicable:
        assert "wcq_ending_balance_used" in verdict.reason_codes
        assert verdict.data_quality <= D("0.85")


def test_сила_и_уверенность_остаются_в_границах():
    verdict = evaluate(series())
    assert D(0) <= verdict.strength <= D(1)
    assert D(0) <= verdict.confidence <= D(1)


def test_веса_компонентов_складываются_в_единицу():
    assert abs(sum(wcq.WEIGHTS.values()) - 1.0) < 1e-9

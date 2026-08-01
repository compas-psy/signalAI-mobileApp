"""Проверка на истории §19.

Ошибка «заглянул в будущее» не выглядит как ошибка: числа сходятся,
результат красив. Поэтому проверки утечки здесь падают, а не
предупреждают, — и тесты проверяют именно падение.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.research.backtest import (
    MIN_CLUSTERS,
    Bar,
    Costs,
    Event,
    LeakageError,
    assert_point_in_time,
    first_executable,
    run_event,
    summarise,
)

D = Decimal
START = datetime(2026, 1, 5, tzinfo=UTC)


def bars(count: int = 800, *, step: D = D("1"), start: D = D(100),
         gaps: set[int] = frozenset()) -> list[Bar]:
    return [
        Bar(
            time=START + timedelta(days=i),
            close=start + step * i,
            tradeable=i not in gaps,
        )
        for i in range(count)
    ]


def event(**overrides) -> Event:
    base = dict(
        hypothesis_id="h-1",
        cluster_id="c-1",
        entity_id="issuer-1",
        instrument_id="MOEX:EQ:TEST",
        as_of=START + timedelta(days=10),
        tradable_at=START + timedelta(days=11),
        detected_at=START + timedelta(days=9),
        feature_available_at=START + timedelta(days=8),
        kpi_expected_direction="up",
        kpi_actual_change=D("0.12"),
        regime="post_2022",
    )
    base.update(overrides)
    return Event(**base)


# ── Утечки ──────────────────────────────────────────────────────────────────


def test_признак_из_будущего_роняет_проверку():
    """Единственная ошибка, при которой результат выглядит лучше правды."""
    with pytest.raises(LeakageError) as exc:
        assert_point_in_time(
            event(
                feature_available_at=START + timedelta(days=20),
                detected_at=START + timedelta(days=9),
            )
        )
    assert "признак доступен" in str(exc.value)


def test_сигнал_позже_гипотезы_роняет_проверку():
    with pytest.raises(LeakageError):
        assert_point_in_time(
            event(detected_at=START + timedelta(days=30),
                  as_of=START + timedelta(days=10))
        )


def test_корректный_порядок_проходит():
    assert_point_in_time(event())


# ── Вход ────────────────────────────────────────────────────────────────────


def test_вход_берётся_после_момента_доступности_а_не_в_момент_публикации():
    """Разница между этими точками и есть весь результат плохих исследований."""
    result = run_event(event(), bars(), bars())
    assert result.entry_time > event().tradable_at


def test_приостановка_торгов_переносит_вход_и_фиксирует_задержку():
    """Задержка — это и есть разница между исследованием и реальностью."""
    result = run_event(event(), bars(gaps={12, 13, 14}), bars())
    assert result.delay_days >= 4
    assert result.executable


def test_невозможный_вход_записывается_а_не_пропускается():
    short = [Bar(time=START, close=D(100))]
    result = run_event(event(), short, short)
    assert not result.executable
    assert "вход невозможен" in result.note


def test_издержки_входят_в_цену_входа():
    """Сравнение «как если бы купили по закрытию» завышает результат."""
    free = run_event(event(), bars(), bars(), costs=Costs(D(0), D(0), D(0)))
    real = run_event(event(), bars(), bars())
    assert real.entry_price > free.entry_price
    assert real.returns[180] < free.returns[180]


# ── Две разные задачи проверки ──────────────────────────────────────────────


def test_экономическая_проверка_независима_от_рыночной():
    """Показатель мог сбыться там, где бумага не выросла.

    Это важный результат, а не досадный: он говорит, что связь с
    экономикой есть, а с ценой — нет.
    """
    flat = [Bar(time=START + timedelta(days=i), close=D(100)) for i in range(800)]
    result = run_event(event(kpi_actual_change=D("0.2")), flat, flat)
    assert result.kpi_hit is True
    assert result.returns[180] < 0  # съедено издержками


def test_несбывшийся_показатель_отмечается():
    result = run_event(event(kpi_actual_change=D("-0.1")), bars(), bars())
    assert result.kpi_hit is False


def test_избыточная_доходность_считается_к_эталону():
    slow = bars(step=D("0.2"))
    fast = bars(step=D("1"))
    result = run_event(event(), fast, slow)
    assert result.excess[180] > 0


# ── Итог по стратегии ───────────────────────────────────────────────────────


def make_results(count: int, *, cluster_prefix="c", regime="post_2022"):
    events, results = [], []
    for i in range(count):
        e = event(hypothesis_id=f"h-{i}", cluster_id=f"{cluster_prefix}-{i}",
                  regime=regime)
        events.append(e)
        results.append(run_event(e, bars(step=D("1")), bars(step=D("0.2"))))
    return events, results


def test_малая_выборка_не_даёт_вывода():
    """Приёмочный критерий §19.6: 25–30 независимых кластеров либо статус."""
    events, results = make_results(5)
    summary = summarise(results, events)
    assert not summary.sufficient_sample
    assert f"из {MIN_CLUSTERS}" in summary.verdict


def test_версии_одной_гипотезы_не_являются_новыми_случаями():
    """Пять сигналов об одной истории — одно наблюдение, а не пять."""
    events, results = make_results(5, cluster_prefix="same")
    for r in results:
        r.cluster_id = "same"
    summary = summarise(results, events)
    assert summary.clusters == 1


def test_один_режим_рынка_не_доказывает_устойчивости():
    """§19.8: результат, созданный одним режимом, не является доказательством."""
    events, results = make_results(30)
    summary = summarise(results, events)
    assert summary.sufficient_sample
    assert "одном режиме" in summary.verdict


def test_разные_режимы_дают_вывод():
    a_events, a_results = make_results(15, cluster_prefix="a", regime="pre_2022")
    b_events, b_results = make_results(15, cluster_prefix="b", regime="post_2022")
    summary = summarise(a_results + b_results, a_events + b_events)
    assert summary.sufficient_sample
    assert summary.verdict == "выборки достаточно для вывода"
    assert set(summary.by_regime) == {"pre_2022", "post_2022"}


def test_результат_без_двух_лучших_событий_считается_отдельно():
    """Если он разваливается, стратегии нет — есть два удачных случая."""
    events, results = make_results(10)
    summary = summarise(results, events)
    assert 180 in summary.median_excess
    assert 180 in summary.median_excess_without_top2


def test_точность_знака_показателя_считается():
    events, results = make_results(4)
    results[0].kpi_hit = False
    summary = summarise(results, events)
    assert summary.kpi_sign_accuracy == D("0.75")

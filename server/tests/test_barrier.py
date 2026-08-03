"""Уверенность §15.4: чем «не измерено» отличается от «плохо».

Владелец три недели видел пустую вкладку «Решения» и справедливо считал
это поломкой продукта. Поломкой оно и было — но не в отборе идей, а в
одной подстановке: неизмеренные компоненты уверенности считались нулями.

Ноль — это утверждение «выборка негодная, калибровка провальная». Правда
была другой: статистики закрытых сделок ещё нет, сравнивать не с чем.
Разница стоила всего продукта: два нуля зануляли 0,55 веса, потолок
достижимого падал ниже порога допуска, и ни одна идея не могла стать
сработавшей ни при каком рынке.
"""

from __future__ import annotations

from decimal import Decimal


def test_неизмеренный_компонент_это_незнание_а_не_ноль():
    """Ворота уверенности были непроходимы арифметически.

    Статистики закрытых сделок ещё нет, и `sample_adequacy` с
    `calibration` подставлялись нулями. Ноль — это приговор («выборка
    негодная»), а правда была другой: «не измерено». Два нуля зануляли
    0,55 веса, максимум достижимого падал до 0,325 при пороге 0,50, и
    ворота не мог пройти никто и никогда — вкладка «Решения» на
    устройстве была пуста структурно.
    """
    from app.probability.barrier import UNKNOWN, ConfidenceInput, confidence

    зануление = confidence(
        ConfidenceInput(
            sample_adequacy=Decimal(0),
            calibration=Decimal(0),
            data_completeness=Decimal(1),
            regime_similarity=Decimal("0.5"),
            stability=Decimal("0.5"),
        )
    )[0]
    незнание = confidence(
        ConfidenceInput(
            sample_adequacy=None,
            calibration=None,
            data_completeness=Decimal(1),
            regime_similarity=Decimal("0.5"),
            stability=Decimal("0.5"),
        )
    )[0]

    assert зануление == Decimal("0.3250")
    assert зануление < Decimal("0.50")          # порог непроходим
    assert незнание >= Decimal("0.50")          # порог достижим
    assert UNKNOWN == Decimal("0.5")


def test_порог_допуска_не_ослаблен():
    """Чинилось вычисление, а не требование."""
    from app.config import get_config

    assert Decimal(str(get_config().get("ideas.min_confidence"))) == Decimal("0.50")


def test_без_калибровки_высокой_уверенности_не_бывает():
    """Именно калибровка отвечает, совпадали ли прошлые оценки с исходом.

    Заявлять высокую уверенность, не зная этого, нельзя ни при каких
    остальных компонентах.
    """
    from app.probability.barrier import ConfidenceInput, confidence

    _, band, _ = confidence(
        ConfidenceInput(
            sample_adequacy=None,
            calibration=None,
            data_completeness=Decimal(1),
            regime_similarity=Decimal(1),
            stability=Decimal(1),
        )
    )
    assert band == "MEDIUM"

    _, измеренная, _ = confidence(
        ConfidenceInput(
            sample_adequacy=Decimal("0.9"),
            calibration=Decimal("0.9"),
            data_completeness=Decimal(1),
            regime_similarity=Decimal(1),
            stability=Decimal(1),
        )
    )
    assert измеренная == "HIGH"


def test_неизмеренное_названо_поимённо():
    """Число 0,60 без списка выглядит суждением, а оно наполовину признание."""
    from app.probability.barrier import (
        ConfidenceInput,
        confidence,
        unmeasured_components,
    )

    _, _, weights = confidence(
        ConfidenceInput(
            sample_adequacy=None,
            calibration=None,
            data_completeness=Decimal(1),
            regime_similarity=Decimal("0.5"),
            stability=Decimal("0.5"),
        )
    )
    assert unmeasured_components(weights) == ["sample_adequacy", "calibration"]

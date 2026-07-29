"""Конфигурация: числа ТЗ на месте, противоречия не проходят загрузку.

Смысл этих тестов — не «yaml читается». Смысл в том, что параметр, которым
считается размер позиции, соответствует документу, и что испорченную
конфигурацию сервер отвергает при старте, а не на сделке.
"""

from __future__ import annotations

import copy
from decimal import Decimal

import pytest
import yaml

from app.config import ConfigError, load_config


@pytest.fixture(scope="module")
def cfg():
    return load_config()


# ─── Числа engine-ТЗ §17 и §27 ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("risk.base_risk_per_trade", "0.005"),
        ("risk.max_risk_per_trade", "0.0075"),
        ("risk.max_total_open_risk", "0.02"),
        ("risk.max_cluster_risk", "0.01"),
        ("risk.daily_loss_limit", "0.015"),
        ("risk.weekly_loss_limit", "0.035"),
        ("risk.monthly_loss_limit", "0.06"),
        ("risk.max_crypto_leverage", "3.0"),
        ("ideas.max_daily_cards", "3"),
        ("ideas.active_probability_min", "0.56"),
        ("ideas.active_expected_r_min", "0.20"),
        ("ideas.min_rr_tp2", "2.0"),
        ("ideas.min_confidence", "0.50"),
        ("portfolio.crypto_max_weight", "0.05"),
        ("probability.rule_prior_cap", "0.68"),
    ],
)
def test_matches_engine_tz(cfg, path, expected):
    assert cfg.decimal(path) == Decimal(expected), path


def test_paper_only_is_on_by_default(cfg):
    """§0.5 и §21: MVP работает в PAPER, боевые заявки по умолчанию закрыты."""
    assert cfg.get("risk.paper_only") is True
    assert cfg.get("execution.mode") == "PAPER"


def test_scoring_weights_sum_to_one_exactly(cfg):
    """§15.1: одиннадцать слагаемых дают ровно 1; data_quality — множитель.

    Проверка идёт в Decimal: во float та же сумма даёт 1.0000000000000002.
    """
    weights = cfg.get("scoring.weights")
    assert sum(Decimal(str(v)) for v in weights.values()) == Decimal("1")
    assert "data_quality" not in weights


def test_scoring_weights_match_tz_verbatim(cfg):
    assert cfg.get("scoring.weights") == {
        "regime_fit": 0.16,
        "market_structure": 0.14,
        "setup_quality": 0.14,
        "trigger_quality": 0.12,
        "derivatives_confirmation": 0.10,
        "volume_confirmation": 0.08,
        "liquidity_quality": 0.07,
        "volatility_fit": 0.06,
        "level_confluence": 0.06,
        "risk_reward_quality": 0.04,
        "execution_quality": 0.03,
    }


def test_drawdown_ladder_ends_with_halt(cfg):
    """§17: просадка больше 10% останавливает торговлю, а не уменьшает объём."""
    ladder = cfg.get("risk.drawdown_scaling")
    assert [Decimal(str(s["multiplier"])) for s in ladder] == [
        Decimal("1.00"),
        Decimal("0.75"),
        Decimal("0.50"),
        Decimal("0"),
    ]


def test_ranking_weights_sum_to_one(cfg):
    """§16.7: веса ранжирования карточек дня."""
    ranking = cfg.get("ideas.ranking")
    assert sum(Decimal(str(v)) for v in ranking.values()) == Decimal("1")


def test_three_strategies_and_no_more(cfg):
    """§10–§12: стратегий ровно три. Четвёртая — расхождение с ТЗ."""
    assert set(cfg.get("strategies")) == {
        "trend_pullback",
        "breakout_retest",
        "wyckoff_reversal",
    }


def test_config_hash_is_stable_and_content_bound(tmp_path):
    """Отпечаток меняется от значений, но не от порядка строк.

    §27 требует хранить `config_hash` у каждой идеи. Если хэш плавает от
    переформатирования файла, связь «идея ↔ параметры» рвётся; если не
    меняется от правки числа — она врёт.
    """
    original = load_config()

    data = yaml.safe_load(open(original.source, encoding="utf-8"))
    reordered = dict(reversed(list(data.items())))
    same = tmp_path / "same.yaml"
    same.write_text(yaml.safe_dump(reordered, allow_unicode=True), encoding="utf-8")
    assert load_config(same).config_hash == original.config_hash

    changed = copy.deepcopy(data)
    changed["risk"]["daily_loss_limit"] = 0.02
    other = tmp_path / "other.yaml"
    other.write_text(yaml.safe_dump(changed, allow_unicode=True), encoding="utf-8")
    assert load_config(other).config_hash != original.config_hash


# ─── Отказ от противоречивой конфигурации ─────────────────────────────────


def _write(tmp_path, mutate):
    data = yaml.safe_load(open(load_config().source, encoding="utf-8"))
    mutate(data)
    path = tmp_path / "broken.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_rejects_weights_that_do_not_sum_to_one(tmp_path):
    path = _write(tmp_path, lambda d: d["scoring"]["weights"].update(regime_fit=0.5))
    with pytest.raises(ConfigError, match="сумма scoring.weights"):
        load_config(path)


def test_rejects_limits_out_of_order(tmp_path):
    """Недельный лимит меньше дневного — дневной перестал бы что-то значить."""
    path = _write(tmp_path, lambda d: d["risk"].update(weekly_loss_limit=0.01))
    with pytest.raises(ConfigError, match="лимиты потерь должны расти"):
        load_config(path)


def test_rejects_base_risk_above_max(tmp_path):
    path = _write(tmp_path, lambda d: d["risk"].update(base_risk_per_trade=0.02))
    with pytest.raises(ConfigError, match="base_risk_per_trade"):
        load_config(path)


def test_rejects_ladder_without_halt(tmp_path):
    """Лестница просадки, которая никогда не останавливает, — не лестница."""

    def mutate(d):
        d["risk"]["drawdown_scaling"][-1]["multiplier"] = 0.25

    with pytest.raises(ConfigError, match="останавливать"):
        load_config(_write(tmp_path, mutate))


def test_rejects_unsorted_ladder(tmp_path):
    def mutate(d):
        d["risk"]["drawdown_scaling"][0]["max_dd"] = 0.9

    with pytest.raises(ConfigError, match="возрастанию"):
        load_config(_write(tmp_path, mutate))


def test_missing_parameter_is_an_error_not_none(cfg):
    """Отсутствующий параметр обязан упасть.

    Тихий None в торговой формуле — это посчитанный не тот размер позиции.
    """
    with pytest.raises(ConfigError, match="нет параметра"):
        cfg.get("risk.no_such_limit")
    assert cfg.get("risk.no_such_limit", default=None) is None

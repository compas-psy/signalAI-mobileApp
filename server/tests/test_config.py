"""Конфигурация: критические risk/safety числа закреплены тестом.

Recovery меняет только product-funnel выдачи: число показываемых карточек,
rule-prior admission threshold и ширину crypto universe. Денежные лимиты,
RR, confidence и PAPER gate остаются прежними.
"""

from __future__ import annotations

import copy
from decimal import Decimal

import pytest
import yaml

from app.config import ConfigError, load_config
from app.portfolio.rebalance import economics_policy_from_config


@pytest.fixture(scope="module")
def cfg():
    return load_config()


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
        # Recovery: пять кандидатов обозримы в personal UI и не меняют risk.
        ("ideas.max_daily_cards", "5"),
        # До собственной OOS-статистики rule-prior не должен выключать весь
        # funnel; RR/confidence/EV/trigger/liquidity остаются отдельными gates.
        ("ideas.active_probability_min", "0.54"),
        ("ideas.active_expected_r_min", "0.20"),
        ("ideas.min_rr_tp2", "2.0"),
        ("ideas.min_confidence", "0.50"),
        ("portfolio.crypto_max_weight", "0.05"),
        ("probability.rule_prior_cap", "0.68"),
    ],
)
def test_recovery_config_and_safety_limits(cfg, path, expected):
    assert cfg.decimal(path) == Decimal(expected), path


def test_recovery_does_not_open_live_trading(cfg):
    assert cfg.get("risk.paper_only") is True
    assert cfg.get("execution.mode") == "PAPER"


def test_recovery_keeps_core_quality_gates(cfg):
    assert cfg.decimal("ideas.min_rr_tp2") >= Decimal("2.0")
    assert cfg.decimal("ideas.min_confidence") >= Decimal("0.50")
    assert cfg.decimal("ideas.active_expected_r_min") >= Decimal("0.20")
    assert cfg.decimal("ideas.watch_probability_min") >= Decimal("0.45")
    assert int(cfg.get("strategies.trend_pullback.min_zones")) >= 2


def test_crypto_recovery_still_requires_meaningful_history(cfg):
    assert int(cfg.get("universe.crypto.min_history_days")) >= 180
    assert int(cfg.get("universe.crypto.max_active")) <= 30


def test_scoring_weights_sum_to_one_exactly(cfg):
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
    ladder = cfg.get("risk.drawdown_scaling")
    assert [Decimal(str(s["multiplier"])) for s in ladder] == [
        Decimal("1.00"),
        Decimal("0.75"),
        Decimal("0.50"),
        Decimal("0"),
    ]


def test_ranking_weights_sum_to_one(cfg):
    ranking = cfg.get("ideas.ranking")
    assert sum(Decimal(str(v)) for v in ranking.values()) == Decimal("1")


def test_three_strategies_and_no_more(cfg):
    assert set(cfg.get("strategies")) == {
        "trend_pullback",
        "breakout_retest",
        "wyckoff_reversal",
    }


def test_config_hash_is_stable_and_content_bound(tmp_path):
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
    path = _write(tmp_path, lambda d: d["risk"].update(weekly_loss_limit=0.01))
    with pytest.raises(ConfigError, match="лимиты потерь должны расти"):
        load_config(path)


def test_rejects_base_risk_above_max(tmp_path):
    path = _write(tmp_path, lambda d: d["risk"].update(base_risk_per_trade=0.02))
    with pytest.raises(ConfigError, match="base_risk_per_trade"):
        load_config(path)


def test_rejects_ladder_without_halt(tmp_path):
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
    with pytest.raises(ConfigError, match="нет параметра"):
        cfg.get("risk.no_such_limit")
    assert cfg.get("risk.no_such_limit", default=None) is None


def test_rebalance_economics_stays_unconfigured_until_all_inputs_are_approved(cfg):
    assert economics_policy_from_config(cfg) is None


def test_rejects_partial_rebalance_economics_policy(tmp_path):
    def mutate(data):
        data["portfolio"]["rebalance_economics"] = {
            "policy_id": "broker-a-v1",
            "fee_bps": 15,
            "capital_gain_tax_rate": None,
        }

    with pytest.raises(ConfigError, match="rebalance_economics требует"):
        load_config(_write(tmp_path, mutate))


def test_complete_rebalance_economics_policy_is_read_from_config(tmp_path):
    def mutate(data):
        data["portfolio"]["rebalance_economics"] = {
            "policy_id": "broker-a-v1",
            "fee_bps": "15",
            "capital_gain_tax_rate": "0.13",
        }

    policy = economics_policy_from_config(load_config(_write(tmp_path, mutate)))
    assert policy is not None
    assert policy.policy_id == "broker-a-v1"
    assert policy.fee_bps == Decimal("15")
    assert policy.capital_gain_tax_rate == Decimal("0.13")

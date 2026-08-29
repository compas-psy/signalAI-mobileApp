from app.control.runtime_roles import compose_runtime_roles


def test_runtime_roles_separate_live_generator_from_measurement_candidates() -> None:
    competition = {
        "control_version": "legacy_control_v1",
        "candidates": [
            {"version": "breakout_v2", "verdict": "WAITING_FOR_SAMPLE"},
            {"version": "crypto_carry_v1", "verdict": "CANDIDATE_WINNING"},
        ],
    }

    roles = compose_runtime_roles(
        competition,
        registry_roles={
            "breakout_v2": "CANDIDATE",
            "crypto_carry_v1": "CHAMPION",
        },
    )

    assert roles["live_generator"] == {
        "version": "legacy_control_v1",
        "publishes_trade_ideas": True,
        "strategy_families": [
            "TREND_PULLBACK",
            "BREAKOUT_RETEST",
            "WYCKOFF_REVERSAL",
        ],
    }
    assert roles["champion"] == "crypto_carry_v1"
    assert roles["challengers"] == ["breakout_v2"]
    assert roles["shadow_only"] == ["breakout_v2", "crypto_carry_v1"]
    assert roles["governance_controls_runtime"] is False


def test_runtime_roles_do_not_call_candidate_live_when_registry_is_empty() -> None:
    competition = {
        "control_version": "legacy_control_v1",
        "candidates": [{"version": "momentum_v2", "verdict": "CANDIDATE_WINNING"}],
    }

    roles = compose_runtime_roles(competition, registry_roles={})

    assert roles["champion"] is None
    assert roles["challengers"] == ["momentum_v2"]
    assert roles["shadow_only"] == ["momentum_v2"]
    assert roles["live_generator"]["version"] == "legacy_control_v1"

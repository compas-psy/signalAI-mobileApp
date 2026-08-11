from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.config import get_config
from app.models.enums import Direction, PaperStatus
from app.paper.management_policy import targets_with_policy
from app.risk.dynamic_exit import advance_v2
from app.risk.engine_v2 import EdgeSnapshot, _mode

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def _trade(*, shares=("0.35", "0.30", "0.35")):
    return SimpleNamespace(
        direction=Direction.LONG,
        status=PaperStatus.PENDING,
        entry=Decimal("100"),
        initial_stop=Decimal("90"),
        current_stop=Decimal("90"),
        tp_prices=["110", "120", "130"],
        tp_shares=list(shares),
        tps_taken=0,
        breakeven_at=None,
        realized_r=Decimal(0),
        opened_at=NOW,
        closed_at=None,
        outcome="",
        close_reason="",
    )


def _bar(index: int, *, low: str, high: str, close: str):
    return SimpleNamespace(
        open_time=NOW + timedelta(hours=index),
        low=Decimal(low),
        high=Decimal(high),
        close=Decimal(close),
    )


def test_low_sample_and_unstable_edge_is_defensive():
    assert _mode(
        EdgeSnapshot(sample_size=5, stability=Decimal("0.8")),
        Decimal("0.80"),
    ) == "defensive"
    assert _mode(
        EdgeSnapshot(
            sample_size=120,
            posterior_win=Decimal("0.61"),
            stability=Decimal("0.72"),
        ),
        Decimal("0.75"),
    ) == "conviction"


def test_tp3_activates_runner_instead_of_closing_position():
    trade = _trade()
    events = advance_v2(
        trade,
        [
            _bar(1, low="99", high="101", close="100"),
            _bar(2, low="101", high="136", close="134"),
        ],
        now=NOW + timedelta(hours=2),
    )

    assert trade.status is PaperStatus.OPEN
    assert trade.tps_taken == 3
    # TP1 + TP2 realised; 35% runner stays open after crossing TP3.
    assert trade.realized_r == Decimal("0.95")
    assert trade.current_stop > trade.entry
    assert any("runner активирован" in event for event in events)
    assert any("runner trail" in event for event in events)


def test_runner_stop_only_ratchets_in_profitable_direction():
    trade = _trade()
    advance_v2(
        trade,
        [
            _bar(1, low="99", high="101", close="100"),
            _bar(2, low="101", high="136", close="134"),
        ],
        now=NOW + timedelta(hours=2),
    )
    first_stop = trade.current_stop

    # A weaker bar cannot loosen an already protected stop.
    advance_v2(
        trade,
        [_bar(3, low=str(first_stop + Decimal("0.1")), high="132", close="131")],
        now=NOW + timedelta(hours=3),
    )
    assert trade.current_stop >= first_stop


def test_alpha_decay_exits_stagnant_v2_trade_before_full_stop():
    # defensive policy has 36h time stop
    trade = _trade(shares=("0.50", "0.35", "0.15"))
    advance_v2(
        trade,
        [
            _bar(1, low="99", high="101", close="100"),
            _bar(38, low="96", high="104", close="98"),
        ],
        now=NOW + timedelta(hours=38),
    )
    assert trade.status is PaperStatus.CLOSED
    assert trade.close_reason == "alpha-decay time stop"
    assert trade.realized_r == Decimal("-0.2")


def test_legacy_404020_trade_keeps_hard_tp3_semantics():
    trade = _trade(shares=("0.40", "0.40", "0.20"))
    advance_v2(
        trade,
        [
            _bar(1, low="99", high="101", close="100"),
            _bar(2, low="101", high="136", close="134"),
        ],
        now=NOW + timedelta(hours=2),
    )
    assert trade.status is PaperStatus.CLOSED
    assert trade.outcome == "TP"


def test_idea_policy_is_snapshotted_into_new_paper_shares():
    idea = SimpleNamespace(
        tp1=Decimal("110"),
        tp2=Decimal("120"),
        tp3=Decimal("130"),
        explanation_json={
            "risk_policy": {"tp_shares": ["0.20", "0.25", "0.55"]}
        },
    )
    prices, shares = targets_with_policy(idea)
    assert prices == [Decimal("110"), Decimal("120"), Decimal("130")]
    assert shares == [Decimal("0.20"), Decimal("0.25"), Decimal("0.55")]


def test_optimizer_candidates_cannot_change_risk_multiplier():
    cfg = get_config()
    for candidate in cfg.get("risk.management.optimizer.candidates"):
        for profile in candidate["profiles"].values():
            assert "risk_multiplier" not in profile


def test_scheduler_has_exactly_one_active_risk_runtime_in_isolated_process():
    """Acceptance gate requested by owner: not two risk calculators/processes."""
    code = """
import json
from app.pipeline.risk_equity_runtime import install
from app.risk.engine_v2 import runtime_report
install()
print(json.dumps(runtime_report(), sort_keys=True))
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report["risk_calculators"] == 1
    assert report["exit_engines"] == 1
    assert report["paper_and_crypto_share_exit_engine"] is True
    assert report["absolute_risk_caps_changed"] is False


def test_api_process_does_not_install_second_risk_engine():
    main_source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
    assert "risk.engine_v2" not in main_source
    assert "install_risk_v2" not in main_source

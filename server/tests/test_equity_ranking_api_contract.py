from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

from app.api.v1.equity_rankings import _out


def _item(rank: int, *, late: bool = False) -> dict:
    return {
        "rank": rank,
        "rank_change": None if rank == 1 else 1,
        "instrument_id": f"MOEX:EQ:T{rank:02d}",
        "symbol": f"T{rank:02d}",
        "title": f"Company {rank}",
        "score": 90.0 - rank,
        "tier": "стоит смотреть" if rank < 4 else "наблюдать",
        "eligible": True,
        "fundamental_score": 72.0,
        "technical_score": 68.0,
        "early_score": 22.0 if late else 81.0,
        "early_state": "поздно / не догонять" if late else "ранняя подготовка",
        "why_now": ["оборот ускоряется"],
        "confirmation": "подтверждение ранней гипотезы",
        "invalidation": "инвалидация ранней гипотезы",
        "momentum_5d": None if rank == 1 else 0.021,
        "momentum_20d": 0.035,
        "compression_ratio": 0.64,
        "breakout_distance_63d": -0.018,
        "turnover_ratio_5v20": 1.42,
        "accumulation_share": 0.66,
        "chase_penalty": 0.91 if late else 0.05,
        "catalyst_adjustment": 0.0,
        "technical_state": "восходящий D1",
        "price": 123.4,
        "momentum_3m": 0.11,
        "momentum_6m": 0.18,
        "drawdown_6m": -0.03,
        "volatility_3m": 0.24,
        "fundamental_facts": ["ROE измерен"],
        "technical_facts": ["цена выше MA50"],
        "warnings": ["движение уже растянуто"] if late else [],
        "hypothesis": None,
    }


def _snapshot(items: list[dict]):
    return SimpleNamespace(
        market_day=date(2026, 8, 17),
        generated_at=datetime(2026, 8, 17, 7, 0, tzinfo=UTC),
        data_as_of=datetime(2026, 8, 14, 20, 0, tzinfo=UTC),
        methodology="equity_rank_v2_early",
        universe_count=len(items),
        scored_count=len(items),
        items_json=items,
    )


def test_equity_ranking_api_keeps_full_universe_and_server_order():
    result = _out(_snapshot([_item(rank) for rank in range(1, 13)]))

    assert len(result.items) == 12
    assert [item.rank for item in result.items] == list(range(1, 13))
    assert [item.symbol for item in result.items] == [f"T{rank:02d}" for rank in range(1, 13)]


def test_equity_ranking_api_exposes_early_contract_without_inventing_nulls():
    result = _out(_snapshot([_item(1), _item(2, late=True)]))
    early, late = result.items

    assert early.early_score == 81.0
    assert early.early_state == "ранняя подготовка"
    assert early.early_eligible is True
    assert early.why_now == ["оборот ускоряется"]
    assert early.confirmation == "подтверждение ранней гипотезы"
    assert early.invalidation == "инвалидация ранней гипотезы"
    assert early.return_5d is None
    assert early.return_20d == 0.035
    assert early.breakout_distance == -0.018
    assert early.turnover_ratio == 1.42
    assert early.accumulation_score == 0.66
    assert early.compression_ratio == 0.64

    assert late.early_state == "поздно / не догонять"
    assert late.early_eligible is False
    assert late.chase_penalty == 0.91
    assert late.warnings == ["движение уже растянуто"]

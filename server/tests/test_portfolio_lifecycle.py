"""Portfolio lifecycle: history, expiry, model diff and scheduled rebuild reasons."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import func, select

from app.models import Instrument, PortfolioModel, PortfolioWeight
from app.models.enums import AssetClass, PackageSize, RiskProfile, Venue
from app.portfolio.build import Package, Position, _persist


def _instrument(session, instrument_id: str) -> None:
    symbol = instrument_id.rsplit(":", 1)[-1]
    session.add(
        Instrument(
            instrument_id=instrument_id,
            venue=Venue.MOEX,
            asset_class=AssetClass.EQUITY,
            symbol=symbol,
            title=symbol,
            currency="RUB",
            tick_size=Decimal("0.01"),
            tick_value=Decimal("0.01"),
            lot_size=1,
            quantity_step=Decimal("1"),
            min_quantity=Decimal("1"),
            contract_multiplier=Decimal("1"),
            is_tradable=True,
            in_universe=True,
            metadata_json={"market": "shares", "board": "TQBR"},
        )
    )
    session.flush()


def _package(instrument_id: str, *, weight: float = 1.0) -> Package:
    return Package(
        profile=RiskProfile.OPTIMAL,
        package=PackageSize.BALANCED,
        horizon_years=1,
        admitted=True,
        reason="ok",
        positions=[
            Position(
                instrument_id=instrument_id,
                title=instrument_id,
                asset_class=AssetClass.EQUITY,
                weight=weight,
                stability=0.01,
                role="рост капитала",
                thesis="test",
                kill="test",
                score=0.8,
                expected_return=0.1,
                evidence={},
            )
        ],
        expected_low=0.05,
        expected_high=0.15,
        volatility=0.10,
        drawdown=0.12,
        cvar_95=0.08,
        rationale="test",
    )


def _model(
    session,
    *,
    generated_at: datetime,
    valid_until: datetime,
    profile: RiskProfile = RiskProfile.OPTIMAL,
    package: PackageSize = PackageSize.BALANCED,
    horizon_years: int = 1,
) -> PortfolioModel:
    model = PortfolioModel(
        profile=profile,
        package=package,
        horizon_years=horizon_years,
        expected_return_low=Decimal("0.05"),
        expected_return_high=Decimal("0.15"),
        target_volatility=Decimal("0.10"),
        model_drawdown_limit=Decimal("0.15"),
        cvar_95=Decimal("0.08"),
        rationale="test",
        stress_json={},
        config_hash="test-config",
        generated_at=generated_at,
        valid_until=valid_until,
    )
    session.add(model)
    session.flush()
    return model


def _weight(session, model: PortfolioModel, instrument_id: str, weight: str) -> None:
    session.add(
        PortfolioWeight(
            model_id=model.id,
            instrument_id=instrument_id,
            asset_class=AssetClass.EQUITY,
            target_weight=Decimal(weight),
            role="рост капитала",
            thesis="test",
            kill_conditions="test",
            evidence_json={},
        )
    )
    session.flush()


def test_rebuild_retains_previous_model_generation(session):
    """A rebuild appends a generation; audit history must not be destroyed."""
    _instrument(session, "MOEX:EQ:AAA")
    cfg = SimpleNamespace(config_hash="test-config")
    first = datetime(2026, 8, 10, 12, tzinfo=UTC)
    second = first + timedelta(days=1)

    _persist(session, _package("MOEX:EQ:AAA"), cfg=cfg, now=first)
    _persist(session, _package("MOEX:EQ:AAA"), cfg=cfg, now=second)
    session.flush()

    count = session.execute(select(func.count()).select_from(PortfolioModel)).scalar_one()
    assert count == 2


def test_current_models_select_latest_unexpired_generation_per_slot(session):
    from app.portfolio.lifecycle import current_models

    now = datetime(2026, 8, 16, 12, tzinfo=UTC)
    _model(
        session,
        generated_at=now - timedelta(days=3),
        valid_until=now - timedelta(hours=1),
    )
    latest = _model(
        session,
        generated_at=now - timedelta(hours=2),
        valid_until=now + timedelta(days=5),
    )
    _model(
        session,
        generated_at=now - timedelta(days=1),
        valid_until=now + timedelta(days=4),
        profile=RiskProfile.CONSERVATIVE,
    )

    models = current_models(session, as_of=now)
    slots = {(m.profile, m.package, m.horizon_years): m for m in models}
    assert slots[(RiskProfile.OPTIMAL, PackageSize.BALANCED, 1)].id == latest.id
    assert len(models) == 2


def test_model_diff_reports_added_removed_and_weight_changes(session):
    from app.portfolio.lifecycle import model_diff

    now = datetime(2026, 8, 16, 12, tzinfo=UTC)
    previous = _model(
        session,
        generated_at=now - timedelta(days=2),
        valid_until=now + timedelta(days=3),
    )
    current = _model(
        session,
        generated_at=now - timedelta(hours=1),
        valid_until=now + timedelta(days=6),
    )
    for instrument_id in ("MOEX:EQ:AAA", "MOEX:EQ:BBB", "MOEX:EQ:CCC"):
        _instrument(session, instrument_id)
    _weight(session, previous, "MOEX:EQ:AAA", "0.60")
    _weight(session, previous, "MOEX:EQ:BBB", "0.40")
    _weight(session, current, "MOEX:EQ:AAA", "0.45")
    _weight(session, current, "MOEX:EQ:CCC", "0.55")

    diff = model_diff(previous, current)
    assert diff.added == ("MOEX:EQ:CCC",)
    assert diff.removed == ("MOEX:EQ:BBB",)
    assert diff.weight_changed == ("MOEX:EQ:AAA",)
    assert diff.material is True


def test_rebuild_due_when_model_nears_expiry_even_without_new_bars(session):
    from app.portfolio.lifecycle import rebuild_due

    now = datetime(2026, 8, 16, 12, tzinfo=UTC)
    _model(
        session,
        generated_at=now - timedelta(days=6),
        valid_until=now + timedelta(hours=12),
    )

    decision = rebuild_due(session, as_of=now, expiry_buffer=timedelta(days=1))
    assert decision.due is True
    assert decision.reason == "model_expiring"

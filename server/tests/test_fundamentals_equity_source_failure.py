from decimal import Decimal
from types import SimpleNamespace

from app.models.enums import AssetClass
from app.portfolio import fundamentals as fund
from app.portfolio.fundamentals import Measure


def _equity():
    return SimpleNamespace(
        instrument_id="MOEX:EQ:TEST",
        asset_class=AssetClass.EQUITY,
        symbol="TEST",
        metadata_json={"issuesize": "1000000"},
    )


def test_dividend_source_failure_is_missing_not_zero(monkeypatch):
    monkeypatch.setattr(fund, "_last_close", lambda *_args, **_kwargs: Decimal("100"))

    def unavailable(*_args, **_kwargs):
        raise ValueError("temporary upstream failure")

    monkeypatch.setattr(fund.moex, "dividends", unavailable)

    metrics = fund._equity_metrics(object(), _equity())
    dividend = next(m for m in metrics if m.name == "dividend_yield")

    assert dividend.status is Measure.MISSING
    assert dividend.value is None
    assert "недоступ" in dividend.text


def test_successful_empty_dividend_history_is_measured_zero(monkeypatch):
    monkeypatch.setattr(fund, "_last_close", lambda *_args, **_kwargs: Decimal("100"))
    monkeypatch.setattr(fund.moex, "dividends", lambda *_args, **_kwargs: ([], {}))

    metrics = fund._equity_metrics(object(), _equity())
    dividend = next(m for m in metrics if m.name == "dividend_yield")

    assert dividend.status is Measure.MEASURED
    assert dividend.value == 0.0

from decimal import Decimal

import pytest

from app.research.adapters import rosstat_prices


def test_footnoted_missing_marker_remains_missing():
    assert rosstat_prices._decimal("…1)") is None


def test_confirmed_numeric_footnote_preserves_two_decimal_places():
    assert rosstat_prices._decimal("12\u00a0471,552)") == Decimal("12471.55")
    assert rosstat_prices._decimal("982509,232)") == Decimal("982509.23")


def test_unrecognized_text_still_fails_closed():
    with pytest.raises(rosstat_prices.WorkbookSchemaError):
        rosstat_prices._decimal("12471,55 примечание")

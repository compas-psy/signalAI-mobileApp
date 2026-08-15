from decimal import Decimal

import pytest

from app.research.adapters import rosstat_prices


# Full live scan of Proizvoditeli_Cena_06-2026.xlsx on 2026-08-15 found only
# two annotated month-cell forms: `…1)` for no value and a numeric value whose
# second decimal place is followed by footnote `2)`. Keep the accepted grammar
# intentionally this narrow so future source changes fail visibly.
def test_footnoted_missing_marker_remains_missing():
    assert rosstat_prices._decimal("…1)") is None


def test_confirmed_numeric_footnote_preserves_two_decimal_places():
    assert rosstat_prices._decimal("12\u00a0471,552)") == Decimal("12471.55")
    assert rosstat_prices._decimal("982509,232)") == Decimal("982509.23")


def test_unrecognized_text_still_fails_closed():
    with pytest.raises(rosstat_prices.WorkbookSchemaError):
        rosstat_prices._decimal("12471,55 примечание")

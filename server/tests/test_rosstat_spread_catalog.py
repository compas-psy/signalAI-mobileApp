"""Contract for discovering the official Rosstat producer-price workbook."""

from __future__ import annotations

import pytest

from app.research.adapters import rosstat_prices


HTML = """
<html><body>
  <a href="/storage/mediabank/ppi-indexes.xlsx">
    Индексы цен производителей по товарам и товарным группам с 1998 г.
  </a>
  <a href="/storage/mediabank/producer-prices.xlsx">
    Средние цены производителей промышленных товаров (услуг) с 1998 г.
  </a>
  <a href="https://rosstat.gov.ru/storage/mediabank/fertilizer-prices.xlsx">
    Средние цены производителей на минеральные удобрения и азотные соединения с 2009 г.
  </a>
</body></html>
"""


def test_discovers_exact_official_producer_price_workbook():
    target = rosstat_prices.discover_workbook(HTML)
    assert target == "https://rosstat.gov.ru/storage/mediabank/producer-prices.xlsx"


def test_similar_dataset_name_does_not_match():
    html = """
    <a href="/storage/mediabank/fertilizer.xlsx">
      Средние цены производителей на минеральные удобрения и азотные соединения с 2009 г.
    </a>
    """
    with pytest.raises(rosstat_prices.DatasetNotFound):
        rosstat_prices.discover_workbook(html)


def test_duplicate_exact_targets_fail_closed():
    html = """
    <a href="/storage/mediabank/a.xlsx">Средние цены производителей промышленных товаров (услуг) с 1998 г.</a>
    <a href="/storage/mediabank/b.xlsx">Средние цены производителей промышленных товаров (услуг) с 1998 г.</a>
    """
    with pytest.raises(rosstat_prices.AmbiguousDataset):
        rosstat_prices.discover_workbook(html)


def test_non_xlsx_target_is_rejected():
    html = """
    <a href="/storage/mediabank/producer-prices.html">
      Средние цены производителей промышленных товаров (услуг) с 1998 г.
    </a>
    """
    with pytest.raises(rosstat_prices.DatasetNotFound):
        rosstat_prices.discover_workbook(html)

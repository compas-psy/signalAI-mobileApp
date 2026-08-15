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

# Reduced from the live 2026-08-15 price catalogue: the current Rosstat page
# renders these workbook anchors with the same icon-only visible text, so the
# producer-price filename is the only stable discriminator among neighbours.
CURRENT_HTML = """
<html><body>
  <a href="/storage/mediabank/Proizvoditeli_Ind_VED_06-2026.xlsx">\ue2c0XLSX</a>
  <a href="/storage/mediabank/Proizvoditeli_Ind_tov_06-2026.xlsx">\ue2c0XLSX</a>
  <a href="/storage/mediabank/Proizvoditeli_Cena_06-2026.xlsx">\ue2c0XLSX</a>
  <a href="/storage/mediabank/Priobretenie_Cena_06-2026.xlsx">\ue2c0XLSX</a>
</body></html>
"""


def test_discovers_exact_official_producer_price_workbook():
    target = rosstat_prices.discover_workbook(HTML)
    assert target == "https://rosstat.gov.ru/storage/mediabank/producer-prices.xlsx"


def test_discovers_current_icon_only_producer_price_filename():
    target = rosstat_prices.discover_workbook(CURRENT_HTML)
    assert target == (
        "https://rosstat.gov.ru/storage/mediabank/"
        "Proizvoditeli_Cena_06-2026.xlsx"
    )


def test_current_filename_match_is_narrow_and_ignores_price_distractors():
    target = rosstat_prices.discover_workbook(CURRENT_HTML)
    assert "Proizvoditeli_Ind_" not in target
    assert "Priobretenie_Cena_" not in target


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


def test_duplicate_current_filename_targets_fail_closed():
    html = """
    <a href="/storage/a/Proizvoditeli_Cena_06-2026.xlsx">\ue2c0XLSX</a>
    <a href="/storage/b/Proizvoditeli_Cena_07-2026.xlsx">\ue2c0XLSX</a>
    """
    with pytest.raises(rosstat_prices.AmbiguousDataset):
        rosstat_prices.discover_workbook(html)


def test_legacy_and_current_different_targets_fail_closed():
    html = f"""
    <a href="/storage/legacy.xlsx">{rosstat_prices.DATASET_TITLE}</a>
    <a href="/storage/Proizvoditeli_Cena_06-2026.xlsx">\ue2c0XLSX</a>
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


def test_external_host_is_rejected_even_with_exact_title():
    html = """
    <a href="https://example.org/producer-prices.xlsx">
      Средние цены производителей промышленных товаров (услуг) с 1998 г.
    </a>
    """
    with pytest.raises(rosstat_prices.DatasetNotFound):
        rosstat_prices.discover_workbook(html)


def test_external_host_is_rejected_even_with_current_filename():
    html = """
    <a href="https://example.org/Proizvoditeli_Cena_06-2026.xlsx">\ue2c0XLSX</a>
    """
    with pytest.raises(rosstat_prices.DatasetNotFound):
        rosstat_prices.discover_workbook(html)


def test_plain_http_is_rejected_even_on_official_host():
    html = """
    <a href="http://rosstat.gov.ru/storage/mediabank/producer-prices.xlsx">
      Средние цены производителей промышленных товаров (услуг) с 1998 г.
    </a>
    """
    with pytest.raises(rosstat_prices.DatasetNotFound):
        rosstat_prices.discover_workbook(html)


def test_plain_http_is_rejected_even_with_current_filename():
    html = """
    <a href="http://rosstat.gov.ru/storage/mediabank/Proizvoditeli_Cena_06-2026.xlsx">\ue2c0XLSX</a>
    """
    with pytest.raises(rosstat_prices.DatasetNotFound):
        rosstat_prices.discover_workbook(html)

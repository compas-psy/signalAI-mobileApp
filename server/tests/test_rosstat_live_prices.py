from __future__ import annotations

from decimal import Decimal

from app.research.adapters import rosstat_live_prices


def test_extended_non_okpd2_source_codes_do_not_block_valid_series():
    rows = [
        [
            "Средние цены производителей промышленных товаров (услуг)\n"
            "по Российской Федерации и федеральным округам в 2026 г."
        ],
        ["на конец периода, рублей за единицу измерения"],
        [
            "",
            "Код товара на основе ОКПД2",
            "Единицы измерения",
            "январь",
            "февраль",
            "март",
        ],
        ["Уголь специальной марки", "05.10.10.101.АГ", "тонн", "", "", ""],
        ["Российская Федерация", "", "", 1, 2, 3],
        ["Бензин автомобильный", "19.20.21.100", "тонн", "", "", ""],
        ["Российская Федерация", "", "", 30205.87, 31480.85, 34924.56],
    ]

    points, matched = rosstat_live_prices.parse_sheets([rows])

    assert matched == 1
    assert {point.product.okpd2 for point in points} == {"19.20.21.100"}
    assert [point.value for point in points] == [
        Decimal("30205.87"),
        Decimal("31480.85"),
        Decimal("34924.56"),
    ]

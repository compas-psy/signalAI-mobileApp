from __future__ import annotations

import pytest

from app.research.adapters import rosstat_prices


def test_product_identity_uses_okpd2_and_okei_as_semantic_key():
    product = rosstat_prices.ProductIdentity.create(
        okpd2=" 02.20.11.110 ",
        okei="121",
        name="  Лесоматериалы   круглые хвойных пород  ",
    )

    assert product.okpd2 == "02.20.11.110"
    assert product.okei == "121"
    assert product.name == "Лесоматериалы круглые хвойных пород"
    assert product.key == ("02.20.11.110", "121")


@pytest.mark.parametrize(
    ("okpd2", "okei", "name"),
    [
        ("", "121", "Товар"),
        ("02.20.AA.110", "121", "Товар"),
        ("02.20.11.110", "12", "Товар"),
        ("02.20.11.110", "121", "   "),
    ],
)
def test_invalid_semantic_identity_fails_closed(okpd2: str, okei: str, name: str):
    with pytest.raises(rosstat_prices.InvalidProductIdentity):
        rosstat_prices.ProductIdentity.create(okpd2=okpd2, okei=okei, name=name)


def test_name_does_not_change_identity_key():
    left = rosstat_prices.ProductIdentity.create(
        okpd2="02.20.11.110",
        okei="121",
        name="Лесоматериалы",
    )
    right = rosstat_prices.ProductIdentity.create(
        okpd2="02.20.11.110",
        okei="121",
        name="Уточнённое наименование",
    )

    assert left.key == right.key

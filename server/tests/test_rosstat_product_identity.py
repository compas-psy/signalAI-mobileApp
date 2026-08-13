from __future__ import annotations

import pytest

from app.research.rosstat_product import InvalidProductIdentity, ProductIdentity


def test_product_identity_uses_okpd2_and_okei_as_semantic_key():
    product = ProductIdentity.create(
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
    with pytest.raises(InvalidProductIdentity):
        ProductIdentity.create(okpd2=okpd2, okei=okei, name=name)


def test_name_does_not_change_identity_key():
    left = ProductIdentity.create(
        okpd2="02.20.11.110",
        okei="121",
        name="Лесоматериалы",
    )
    right = ProductIdentity.create(
        okpd2="02.20.11.110",
        okei="121",
        name="Уточнённое наименование",
    )

    assert left.key == right.key

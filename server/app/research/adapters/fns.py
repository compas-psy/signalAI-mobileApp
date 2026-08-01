"""Адаптер открытых наборов ФНС (ТЗ §6, §10.3).

Четыре набора по ИНН, и вместе они дают то, ради чего движок SUPPLIER
существует наполовину: как живёт поставщик. Доходы и расходы, численность,
уплаченные налоги — ежегодно; налоговая задолженность — ежеквартально.

Чего эти наборы **не** дают: заключённых контрактов, исполнения, авансов и
приёмки. Это ЕИС, и без него движок видит финансовое состояние поставщика,
но не заказы. Покрытие честно помечено как частичное — обещать движку
данные, которых нет, хуже, чем не обещать ничего.

Правовой режим самый простой из всех: открытые данные используются без
договора и лицензий, в том числе коммерчески. Обязательны три вещи —
ссылка на ФНС, законная цель и достоверное представление. Первое и третье
выполняются здесь: ссылка сохраняется с каждым наблюдением, а значения не
пересчитываются и не сглаживаются.

Про годовые данные и точечность. Набор за 2025 год публикуется в 2026-м, и
разрыв бывает больше года. Использовать годовое значение с 1 января
следующего года — классический способ заглянуть в будущее, поэтому
``published_at`` берётся из паспорта набора, а не выводится из периода.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree

from ..collect import Fetched, RemoteObject
from ..policy import Permit
from ..timeline import Availability, tradable_at

SOURCE_ID = "fns_open_data"
BASE_URL = "https://file.nalog.ru/opendata"

# Четыре набора и их частота. Разделены намеренно: у них разные периоды,
# разные схемы и разное время публикации, и сливать их в один «источник
# ФНС» значит потерять точечность у трёх из четырёх.
DATASETS: dict[str, str] = {
    "revenue_expenses": "yearly",
    "headcount": "yearly",
    "paid_taxes": "yearly",
    "tax_debt": "quarterly",
}

# Как называются поля в выгрузках. Список, а не одно имя: ФНС меняет
# написание между наборами и годами, и падать на этом нельзя.
_INN_TAGS = ("ИННЮЛ", "ИНН", "innul", "inn")
_VALUE_TAGS = {
    "revenue_expenses": ("СумДоход", "СумДоходов", "Доход"),
    "headcount": ("КолРаб", "СЧР", "Численность"),
    "paid_taxes": ("СумУплНал", "СумНал", "Сумма"),
    "tax_debt": ("СумНедоим", "СумЗадолж", "Недоимка"),
}
_EXPENSE_TAGS = ("СумРасход", "СумРасходов", "Расход")


@dataclass(frozen=True, slots=True)
class EntityMetric:
    """Показатель одного юридического лица за период."""

    inn: str
    dataset: str
    period_end: date
    value: Decimal | None
    # У набора доходов и расходов две величины в одной записи.
    secondary_value: Decimal | None = None
    unit: str = "RUB"

    @property
    def measured(self) -> bool:
        return self.value is not None


# Адрес паспорта набора. Не файла: имя файла содержит дату выпуска и дату
# версии структуры (`data-20260725-structure-20180110.zip`) и меняется при
# каждой публикации. Собранный по шаблону адрес выглядит правдоподобно и
# отвечает 404 — ровно это и показал живой прогон по всем четырём наборам.
PASSPORTS: dict[str, str] = {
    "revenue_expenses": "https://www.nalog.gov.ru/opendata/7707329152-revexp/",
    "headcount": "https://www.nalog.gov.ru/opendata/7707329152-sshr2019/",
    "paid_taxes": "https://www.nalog.gov.ru/opendata/7707329152-paytax/",
    "tax_debt": "https://www.nalog.gov.ru/opendata/7707329152-debtam/",
}


def passport_urls(datasets: tuple[str, ...] = tuple(DATASETS)) -> list[RemoteObject]:
    """Страницы паспортов, с которых начинается сбор."""
    return [
        RemoteObject(url=PASSPORTS[name], kind=name)
        for name in datasets
        if name in PASSPORTS
    ]


def links_from_passport(html: str, base: str = "") -> dict[str, str]:
    """Вытащить из паспорта адреса выгрузки и структуры.

    Ищутся не строки таблицы по номеру, а расширения файлов. Номер строки
    — свойство вёрстки: ведомство переставит поля местами, и разбор по
    восьмой и десятой строке начнёт молча брать не то. Расширение —
    свойство самого файла.

    Берётся первое совпадение: паспорт перечисляет версии по убыванию
    свежести, и первая ссылка — актуальная публикация.
    """
    from urllib.parse import urljoin

    found: dict[str, str] = {}
    for raw in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
        url = urljoin(base, raw.strip())
        lowered = url.lower()
        if lowered.endswith(".zip") and "data" not in found:
            found["data"] = url
        elif lowered.endswith(".xsd") and "structure" not in found:
            found["structure"] = url
        elif lowered.endswith(".csv") and "csv" not in found:
            found["csv"] = url
    return found


def discover(
    datasets: tuple[str, ...] = tuple(DATASETS),
    passports: dict[str, str] | None = None,
) -> list[RemoteObject]:
    """Наборы, которые предстоит забрать.

    Без прочитанных паспортов возвращаются сами паспорта: следующий шаг
    сбора — прочитать их. Возвращать здесь выдуманные имена файлов значило
    бы обещать план, который не сработает, и узнать об этом только по
    пустому экрану.
    """
    pages = passports or {}
    result: list[RemoteObject] = []
    for name in datasets:
        if name not in DATASETS:
            continue
        html = pages.get(name)
        if html is None:
            result.append(RemoteObject(url=PASSPORTS[name], kind=f"{name}:passport"))
            continue
        links = links_from_passport(html, PASSPORTS[name])
        # Структура идёт первой намеренно: она маленькая, и если молчит
        # именно она, дело не в размере выгрузки.
        if "structure" in links:
            result.append(
                RemoteObject(url=links["structure"], kind=f"{name}:structure")
            )
        if "data" in links:
            result.append(RemoteObject(url=links["data"], kind=name))
    return result


def _decimal(raw: str | None) -> Decimal | None:
    """Число или None.

    Пустое поле в выгрузке ФНС означает «не раскрыто», а не ноль. Разница
    существенная: компания без задолженности и компания, о задолженности
    которой не сообщили, — разные новости.
    """
    if raw is None:
        return None
    text = raw.strip().replace(",", ".").replace("\xa0", "").replace(" ", "")
    if not text or text in ("-", "—"):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _own(element: ElementTree.Element, tags: tuple[str, ...]) -> str | None:
    """Значение на самом элементе или его прямом ребёнке.

    Так опознаётся **граница записи**. Искать ИНН по всему поддереву
    нельзя: тогда корень файла «найдёт» ИНН первой попавшейся компании и
    станет ещё одной записью с чужими числами.
    """
    for tag in tags:
        if tag in element.attrib:
            return element.attrib[tag]
        child = element.find(tag)
        if child is not None and child.text and child.text.strip():
            return child.text
    return None


def _first(element: ElementTree.Element, tags: tuple[str, ...]) -> str | None:
    """Значение где-либо внутри записи.

    По поддереву, а не по одному уровню, и это не перестраховка: в
    выгрузках ФНС опознавательные поля стоят атрибутами на «Документе», а
    сами величины — атрибутами вложенного «СвНП». Разбор, смотрящий только
    на один уровень, находит ИНН и не находит ни одного числа.

    Границу поддерева задаёт вызывающий, и она — запись одной компании:
    выход за неё означал бы приписать одному юридическому лицу показатели
    другого.
    """
    for tag in tags:
        for node in element.iter():
            if tag in node.attrib:
                return node.attrib[tag]
            if node.tag == tag and node.text and node.text.strip():
                return node.text
    return None


def parse_xml(
    data: bytes, *, dataset: str, period_end: date
) -> list[EntityMetric]:
    """Разобрать один XML-файл набора.

    Записи без ИНН отбрасываются: показатель, который не к кому отнести, не
    является наблюдением — сопоставить его потом будет нечем.
    """
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return []

    value_tags = _VALUE_TAGS.get(dataset, ())
    result: list[EntityMetric] = []
    # Записью считается узел, несущий ИНН на себе или на прямом ребёнке.
    # Фиксированный путь не годится: вложенность у наборов разная. Поиск по
    # всему поддереву тоже не годится — корень файла тогда становится ещё
    # одной записью с числами первой попавшейся компании.
    for node in root.iter():
        inn = _own(node, _INN_TAGS)
        if not inn:
            continue
        value = _decimal(_first(node, value_tags))
        secondary = (
            _decimal(_first(node, _EXPENSE_TAGS))
            if dataset == "revenue_expenses"
            else None
        )
        if value is None and secondary is None:
            continue
        result.append(
            EntityMetric(
                inn=inn.strip(),
                dataset=dataset,
                period_end=period_end,
                value=value,
                secondary_value=secondary,
                unit="people" if dataset == "headcount" else "RUB",
            )
        )
    return result


def parse(
    fetched: Fetched, *, dataset: str, period_end: date
) -> list[EntityMetric]:
    """Разобрать выгрузку — архив или отдельный XML.

    Архив разбирается потоком по файлам: наборы ФНС большие, и распаковка
    целиком в память роняет прогон на первом же крупном годе.
    """
    body = fetched.body
    if not body[:2] == b"PK":
        return parse_xml(body, dataset=dataset, period_end=period_end)

    result: list[EntityMetric] = []
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".xml"):
                continue
            with archive.open(name) as handle:
                result.extend(
                    parse_xml(handle.read(), dataset=dataset, period_end=period_end)
                )
    return result


def availability(
    *, published_at: datetime | None, first_seen_at: datetime
) -> Availability:
    """С какого момента набором можно пользоваться.

    Из паспорта набора, а не из периода. Данные за 2025 год публикуются в
    2026-м, и разрыв бывает больше года: считать их доступными с 1 января —
    классический способ заглянуть в будущее.
    """
    return tradable_at(published_at=published_at, first_seen_at=first_seen_at)


def fetch_plan(permit: Permit) -> list[RemoteObject]:
    """Что разрешено забрать этим пропуском."""
    if "fetch" not in permit.operations:
        raise PermissionError(f"{SOURCE_ID}: пропуск не даёт права на загрузку")
    return discover()


def attribution() -> str:
    """Ссылка на источник — обязательное условие использования."""
    return "Источник: Федеральная налоговая служба, открытые данные"


__all__ = [
    "BASE_URL",
    "DATASETS",
    "EntityMetric",
    "SOURCE_ID",
    "attribution",
    "availability",
    "discover",
    "fetch_plan",
    "parse",
    "parse_xml",
]

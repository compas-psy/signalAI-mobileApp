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


#: Каталог открытых данных. Отвечает 200 и перечисляет все наборы с их
#: настоящими идентификаторами — в отличие от файлового хоста, который на
#: каталог не отвечает вовсе.
CATALOGUE = "https://www.nalog.gov.ru/opendata/"

#: По какому куску идентификатора узнаётся набор. Идентификаторы ФНС имеют
#: вид `7707329152-revexp`; полный адрес паспорта берётся из каталога, а не
#: собирается здесь — год в имени набора меняется (`sshr`, `sshr2019`), и
#: угаданный адрес отвечает 404, что снаружи выглядит как пустой источник.
DATASET_KEYS: dict[str, tuple[str, ...]] = {
    "revenue_expenses": ("revexp",),
    "headcount": ("sshr",),
    "paid_taxes": ("paytax",),
    "tax_debt": ("debtam", "debtpp"),
}

# Адрес паспорта набора. Не файла: имя файла содержит дату выпуска и дату
# версии структуры (`data-20260725-structure-20180110.zip`) и меняется при
# каждой публикации. Собранный по шаблону адрес выглядит правдоподобно и
# отвечает 404 — ровно это и показал живой прогон по всем четырём наборам.
#
# Значения здесь — запасной вариант на случай недоступного каталога, а не
# источник правды. Правда приходит из `passports_from_catalogue`.
PASSPORTS: dict[str, str] = {
    "revenue_expenses": f"{CATALOGUE}7707329152-revexp",
    "headcount": f"{CATALOGUE}7707329152-sshr2019",
    "paid_taxes": f"{CATALOGUE}7707329152-paytax",
    "tax_debt": f"{CATALOGUE}7707329152-debtam",
}


def passports_from_catalogue(html: str) -> dict[str, str]:
    """Найти в каталоге адреса паспортов нужных наборов.

    Совпадение по куску идентификатора, а не по полному имени: ФНС
    добавляет год в имя набора при смене структуры, и жёсткое имя ломается
    молча — набор просто перестаёт находиться.
    """
    from urllib.parse import urljoin

    ссылки = [
        urljoin(CATALOGUE, raw)
        for raw in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
        if "opendata/7707329152-" in raw.lower()
    ]
    found: dict[str, str] = {}
    for name, keys in DATASET_KEYS.items():
        for url in ссылки:
            хвост = url.rstrip("/").rsplit("-", 1)[-1].lower()
            if any(хвост.startswith(key) for key in keys):
                found[name] = url
                break
    return found


def passport_urls(
    datasets: tuple[str, ...] = tuple(DATASETS),
    catalogue_html: str = "",
) -> list[RemoteObject]:
    """Страницы паспортов, с которых начинается сбор.

    Из каталога, если он прочитан; из запасного списка, если нет. Второе
    честнее, чем ничего, и хуже, чем первое: запасной адрес может успеть
    устареть между выкладками.
    """
    known = {**PASSPORTS, **passports_from_catalogue(catalogue_html)}
    return [
        RemoteObject(url=known[name], kind=name)
        for name in datasets
        if name in known
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


def _metric(
    node: ElementTree.Element, *, dataset: str, period_end: date
) -> EntityMetric | None:
    """Показатель одной записи — или ничего.

    Вынесено отдельно, потому что разбор идёт двумя путями: целиком из
    памяти для маленьких файлов и потоком для стомегабайтных архивов. Две
    копии этой логики разошлись бы, и разошлись бы молча.
    """
    inn = _own(node, _INN_TAGS)
    if not inn:
        return None
    value = _decimal(_first(node, _VALUE_TAGS.get(dataset, ())))
    secondary = (
        _decimal(_first(node, _EXPENSE_TAGS))
        if dataset == "revenue_expenses"
        else None
    )
    if value is None and secondary is None:
        return None
    return EntityMetric(
        inn=inn.strip(),
        dataset=dataset,
        period_end=period_end,
        value=value,
        secondary_value=secondary,
        unit="people" if dataset == "headcount" else "RUB",
    )


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
        metric = _metric(node, dataset=dataset, period_end=period_end)
        if metric is not None:
            result.append(metric)
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


def parse_archive(
    path, *, dataset: str, period_end: date
) -> list[EntityMetric]:
    """Разобрать архив с диска, не читая его в память целиком.

    Живой прогон дал размеры: 104, 98, 218 и 75 мегабайт. На сервере с
    восемью гигабайтами, где рядом база и модель, `BytesIO` на таком
    архиве — не замедление сбора, а выселение базы.

    `zipfile` умеет читать члены по одному прямо из файла, и распакованный
    XML тоже отдаётся потоком: у ФНС внутри архива лежат части по сотне
    мегабайт, и разворачивать их целиком незачем.
    """
    result: list[EntityMetric] = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".xml"):
                continue
            with archive.open(name) as handle:
                result.extend(
                    parse_stream(handle, dataset=dataset, period_end=period_end)
                )
    return result


def parse_stream(handle, *, dataset: str, period_end: date) -> list[EntityMetric]:
    """Разобрать XML по мере чтения.

    `iterparse` с очисткой разобранных узлов: без неё дерево целиком
    остаётся в памяти, и выигрыш от потокового чтения теряется на
    последнем шаге.
    """
    result: list[EntityMetric] = []
    try:
        for _, element in ElementTree.iterparse(handle, events=("end",)):
            metric = _metric(element, dataset=dataset, period_end=period_end)
            if metric is not None:
                result.append(metric)
                # Разобранный узел больше не нужен. Без очистки дерево
                # остаётся в памяти целиком, и выигрыш от потокового
                # чтения теряется на последнем шаге.
                element.clear()
    except ElementTree.ParseError:
        # Оборванный архив разбирается до места обрыва. Отдать то, что
        # прочиталось, честнее, чем потерять всё из-за хвоста, — а
        # неполнота видна по количеству записей.
        return result
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

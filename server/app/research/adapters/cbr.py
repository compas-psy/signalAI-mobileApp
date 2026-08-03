"""Адаптер сервиса данных Банка России (ТЗ §6, §10).

Что отсюда берётся. Мониторинг предприятий — опережающий показатель спроса:
предприятия отвечают об изменении заказов и ожиданиях раньше, чем это
дойдёт до отчётности. Плюс курс, ключевая ставка и отраслевые индикаторы.

Правовой режим зафиксирован: автоматическое получение данных собственным
кодом прямо предусмотрено, внутренняя аналитика и производные показатели
разрешены, перепродажа сырого массива — нет. Условия могут быть изменены в
одностороннем порядке, поэтому проверка назначена на ноябрь, и после неё
шлюз закроет источник сам, если её не повторить.

Отдельно про время. У наблюдения ЦБ два разных времени, и путать их
нельзя: период, который описывает показатель (например, июль), и дата
публикации (например, середина августа). ``tradable_at`` считается от
второй, а не от первой — иначе проверка на истории будет знать июльские
цифры в июле.

Ограничение частоты — один запрос в секунду с запасом на два. Оно не
формальность: превысить лимит официального источника значит получить отказ
и подойти к блокировке, а альтернативного маршрута к этим данным нет.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from ..collect import Fetched, RemoteObject
from ..policy import Permit
from ..timeline import Availability, tradable_at

SOURCE_ID = "cbr_data"
BASE_URL = "https://www.cbr.ru/dataservice"

# Наборы, которые нужны движкам. Идентификаторы наборов у сервиса числовые;
# здесь они названы по смыслу, а сопоставление живёт в конфигурации —
# иначе смена номера набора превращается в правку кода.
DATASETS = {
    "enterprise_monitoring": "мониторинг предприятий: спрос и ожидания",
    "key_rate": "ключевая ставка",
    "fx_usd_rub": "официальный курс доллара",
    "industry_indicators": "отраслевые индикаторы",
}


@dataclass(frozen=True, slots=True)
class Datum:
    """Одно значение из сервиса данных.

    ``period_end`` — за какой период показатель, ``published_at`` — когда он
    стал публичным. Разные вещи, и вторая определяет, с какого момента
    значение разрешено в проверке на истории.
    """

    dataset: str
    indicator: str
    period_start: date | None
    period_end: date | None
    value: Decimal | None
    unit: str = ""
    region: str = ""
    industry: str = ""
    published_at: datetime | None = None

    @property
    def measured(self) -> bool:
        return self.value is not None


def discover(datasets: tuple[str, ...] = tuple(DATASETS)) -> list[RemoteObject]:
    """Что предстоит забрать.

    Отдельным шагом, потому что §10.1 разделяет «узнать, что есть» и
    «забрать»: первое дешёвое и его можно делать часто, второе стоит
    лимита.

    Начинается с `publications`, а не с корня. Корень `/dataservice/`
    отвечает 404, и это ничего не значит: сервис данных состоит из
    именованных методов, а не из индексной страницы. Собранный адрес
    `/dataservice/data?dataset=...` отвечал 501 на все четыре набора —
    метода с таким именем в сервисе просто нет.

    Публикации перечисляют доступные наборы; идентификаторы для запроса
    данных берутся оттуда, а не из имени набора у нас.
    """
    return [RemoteObject(url=f"{BASE_URL}/publications", kind="publications")]


def datasets_of(publication_id: int) -> str:
    """Адрес списка наборов внутри публикации.

    Идентификатор числовой и приходит из `publications`. Подставлять сюда
    наше имя набора бессмысленно: у сервиса своя нумерация, и живой прогон
    это показал — `data?dataset=key_rate` отвечал 501 просто потому, что
    метода `data` в сервисе нет.
    """
    return f"{BASE_URL}/datasets?publicationId={int(publication_id)}"


def data_of(publication_id: int, dataset_id: int, *, y1: int, y2: int) -> str:
    """Адрес самих значений набора.

    Недостающее звено цепочки. Сбор доходил до списка наборов и разбирал
    **его** как данные: у каждой строки было название показателя и не было
    ни числа, ни периода — потому что это описание набора, а не факт. В
    журнале это выглядело как «забрано 11/11, наблюдений 0», то есть сеть
    открыта, источник отвечает, а наблюдений нет.

    Годы обязательны: сервис отдаёт ряд за диапазон, и без него запрос
    смысла не имеет.
    """
    return (
        f"{BASE_URL}/data?y1={int(y1)}&y2={int(y2)}"
        f"&publicationId={int(publication_id)}&datasetId={int(dataset_id)}"
    )


def dataset_ids(payload: bytes) -> list[int]:
    """Числовые идентификаторы наборов внутри публикации.

    Читаются из ответа, а не собираются из наших имён: нумерация —
    собственность сервиса, и она уже один раз оказалась не той, которую мы
    придумали.
    """
    out: list[int] = []
    for row in publications(payload):
        raw = row.get("id") if row.get("id") is not None else row.get("datasetId")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value not in out:
            out.append(value)
    return out


def shape_of(fetched: Fetched) -> str:
    """Как выглядит ответ, если разобрать его не удалось.

    Нужна затем, что «разобрано 0 строк» не отвечает на вопрос почему.
    Контракт источника меняется, и следующий шаг должен делаться по факту
    ответа, а не по догадке о нём. Возвращаются только имена полей —
    значения в журнал не попадают.
    """
    try:
        payload = json.loads(fetched.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return f"не JSON, первые байты {fetched.body[:60]!r}"
    if isinstance(payload, dict):
        keys = ", ".join(sorted(payload)[:8])
        # Сначала известные имена контейнера, потом любой список объектов.
        # Второе важнее первого: неизвестное имя — это как раз тот случай,
        # ради которого форма и нужна, и отвечать на него «нет строк»
        # значит промолчать ровно там, где нас спросили.
        rows = None
        for name in ("RawData", "rawData", "data", "items"):
            value = payload.get(name)
            if isinstance(value, list) and value and isinstance(value[0], dict):
                rows = value[0]
                break
        if rows is None:
            for value in payload.values():
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    rows = value[0]
                    break
        inner = (
            ", ".join(sorted(rows)[:10]) if isinstance(rows, dict) else "нет строк"
        )
        return f"объект с полями [{keys}]; строка: [{inner}]"
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return f"список; строка: [{', '.join(sorted(payload[0])[:10])}]"
    return f"список из {len(payload)} элементов" if isinstance(payload, list) else "пусто"


def leaf_publications(payload: bytes) -> list[dict]:
    """Публикации, у которых есть данные.

    Ответ — дерево категорий: `NoActive: 1` означает узел-раздел, ноль —
    лист с данными. Забирать разделы бессмысленно, а отличить их можно
    только здесь: снаружи и то и другое выглядит публикацией.
    """
    return [
        item
        for item in publications(payload)
        if not item.get("NoActive") and item.get("id") is not None
    ]


#: По каким словам публикация узнаётся среди дерева категорий. Ключевые
#: слова, а не номера: нумерация — собственность сервиса, и она уже один
#: раз оказалась не той, которую мы придумали.
PUBLICATION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "enterprise_monitoring": ("мониторинг предприят", "деловой климат"),
    "key_rate": ("ключевая ставка",),
    "fx_usd_rub": ("курс",),
    "industry_indicators": ("отрасл",),
}


def match_publications(payload: bytes) -> dict[str, list[int]]:
    """Найти в дереве публикаций листья наших наборов.

    Ответ сервиса — дерево категорий с parent_id, и имя листа само по
    себе ничего не значит: «В целом по Российской Федерации» встречается
    под десятком разделов. Совпадение ищется по полному пути от корня.
    """
    rows = publications(payload)
    names = {r.get("id"): str(r.get("category_name", "")) for r in rows}
    parents = {r.get("id"): r.get("parent_id") for r in rows}

    def path(node_id) -> str:
        parts, current, hops = [], node_id, 0
        while current in names and hops < 10:
            parts.append(names[current])
            current = parents.get(current)
            hops += 1
        return " / ".join(reversed(parts)).lower()

    found: dict[str, list[int]] = {}
    for row in rows:
        if row.get("NoActive") or row.get("id") is None:
            continue
        full = path(row["id"])
        for dataset, keys in PUBLICATION_KEYWORDS.items():
            if any(k in full for k in keys):
                found.setdefault(dataset, []).append(int(row["id"]))
    return found


def publications(payload: bytes) -> list[dict]:
    """Разобрать список публикаций.

    Проверяется, что пришёл именно JSON: сервис данных на неверный метод
    отвечает HTML-страницей с кодом 200, и попытка прочитать её как список
    наборов даёт пустоту — неотличимую от «наборов нет».
    """
    import json

    try:
        body = json.loads(payload.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    if isinstance(body, dict):
        body = body.get("publications") or body.get("items") or []
    return [item for item in body if isinstance(item, dict)]


def _decimal(raw: object) -> Decimal | None:
    """Число или None. Пустая строка и прочерк — это отсутствие, а не ноль."""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", ".").replace("\xa0", "").replace(" ", "")
    if text in ("", "-", "—", "n/a", "null"):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _day(raw: object) -> date | None:
    if raw is None:
        return None
    text = str(raw).strip()
    for pattern in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m", "%Y"):
        try:
            parsed = datetime.strptime(text, pattern)
        except ValueError:
            continue
        if pattern == "%Y":
            return date(parsed.year, 12, 31)
        if pattern == "%Y-%m":
            return date(parsed.year, parsed.month, 1)
        return parsed.date()
    return None


def _moment(raw: object) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        day = _day(text)
        return (
            datetime.combine(day, datetime.min.time(), tzinfo=UTC) if day else None
        )
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse(fetched: Fetched, *, dataset: str) -> list[Datum]:
    """Разобрать ответ сервиса в значения.

    Неизвестные поля не роняют разбор и не выбрасываются молча: контракт
    источника меняется, и адаптер, падающий на новом поле, останавливает
    сбор целиком. Значение, которое не удалось прочитать, становится
    ``None`` — то есть «не измерено», а не нулём.
    """
    try:
        payload = json.loads(fetched.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []

    rows = _rows_of(payload)
    if not rows:
        return []

    result: list[Datum] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        result.append(
            Datum(
                dataset=dataset,
                indicator=str(
                    row.get("indicator")
                    or row.get("elname")
                    or row.get("element_name")
                    or row.get("name")
                    or ""
                ),
                period_start=_day(row.get("period_start") or row.get("dt_from")),
                period_end=_day(
                    row.get("period_end")
                    or row.get("dt")
                    or row.get("date")
                    or _period_from_parts(row)
                ),
                # `obs_val` — как значение называет сам сервис данных в
                # ответе метода `data`. Прежний разбор его не знал, потому
                # что и до метода не доходил.
                value=_decimal(
                    row.get("obs_val")
                    if row.get("obs_val") is not None
                    else (row.get("value") or row.get("val"))
                ),
                unit=str(row.get("unit") or row.get("unit_name") or ""),
                region=str(row.get("region") or ""),
                industry=str(row.get("industry") or row.get("okved") or ""),
                published_at=_moment(row.get("published_at") or row.get("publish_dt")),
            )
        )
    return result


def _rows_of(payload: object) -> list:
    """Строки значений, как бы их ни назвал сервис.

    Имена разные у разных методов, и перебор здесь честнее, чем одно
    угаданное: неизвестное имя даёт пустой разбор, а он попадает в отчёт
    вместе с формой ответа — то есть следующий шаг делается по факту.
    """
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for name in ("RawData", "rawData", "data", "items", "dataset"):
        rows = payload.get(name)
        if isinstance(rows, list):
            return rows
    return []


def _period_from_parts(row: dict) -> str:
    """Период, собранный из года и месяца/квартала по отдельности.

    Сервис отдаёт их разными полями, а не одной датой, и без сборки период
    остаётся пустым — то есть наблюдение выглядит вневременным и в правиле
    3–2–1 не считается ни за какой период.
    """
    year = row.get("year") or row.get("dt_year")
    if year is None:
        return ""
    month = row.get("month") or row.get("dt_month")
    if month is not None:
        return f"{int(year):04d}-{int(month):02d}"
    quarter = row.get("quarter") or row.get("dt_quarter")
    if quarter is not None:
        return f"{int(year):04d}-{int(quarter) * 3:02d}"
    return f"{int(year):04d}"


def availability(datum: Datum, *, first_seen_at: datetime) -> Availability:
    """С какого момента значением можно пользоваться.

    От публикации, а не от периода. Показатель за июль существует с конца
    июля, но узнать о нём можно только в августе, и проверка, использующая
    его в июле, знает будущее.
    """
    return tradable_at(
        published_at=datum.published_at, first_seen_at=first_seen_at
    )


def fetch_plan(permit: Permit) -> list[RemoteObject]:
    """Что разрешено забрать этим пропуском.

    Пропуск требуется аргументом: адаптер не может сходить в сеть, не
    получив его, — это не соглашение, а сигнатура.
    """
    if "fetch" not in permit.operations:
        raise PermissionError(
            f"{SOURCE_ID}: пропуск не даёт права на загрузку"
        )
    return discover()


__all__ = [
    "BASE_URL",
    "DATASETS",
    "Datum",
    "SOURCE_ID",
    "availability",
    "data_of",
    "dataset_ids",
    "discover",
    "fetch_plan",
    "parse",
    "shape_of",
]

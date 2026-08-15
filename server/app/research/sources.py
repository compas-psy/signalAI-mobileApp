"""Реестр источников исследовательского контура (ТЗ Early Signals §6, §7).

Записи заполнены по разделам 6–7 ТЗ и уточнены картой бесплатных маршрутов,
которую дал владелец. Уточнение принципиальное: «данные видны бесплатно» и
«данные можно бесплатно забирать автоматически» — разные вещи, и половина
первоначального списка попала не в ту колонку.

Что из этого следует практически. Ядро первой версии собирается **без
покупки данных**: бюджетный импульс, разведка по поставщикам, динамика
найма, отраслевой спрос, сырьевые спреды и основная криптоаналитика имеют
официальные бесплатные маршруты. Платить приходится ровно за две вещи —
полную бухгалтерскую отчётность по ИНН и биржевые котировки, — и обе
обходятся законно: первая ручной загрузкой по важным компаниям, вторая
через API брокера, токен которого у приложения уже есть.

Три замены, которые стоит держать в голове, потому что они неочевидны:

* **hh.ru → «Работа России».** Условия hh ограничивают использование API
  тематикой трудоустройства; инвестиционная аналитика в неё не входит.
  Государственный портал даёт тот же предмет — вакансии и структуру
  найма — официальным бесплатным API.
* **MOEX ISS → API брокера.** Задержанные данные видны без подписки, но
  биржа прямо запрещает применять их для извлечения прибыли без договора.
  Т-Инвестиции отдают свечи и стаканы по токену брокерского счёта — в
  рамках условий самого брокера, и этот путь законен.
* **БФО ФНС → открытые наборы ФНС.** Полный REST-API отчётности платный,
  но открытые наборы бесплатны и дают по ИНН динамику доходов, расходов,
  численности, налоговых платежей и задолженности. Баланс они не заменяют,
  а для разведки по поставщикам этого уже много.

Статус ``approved`` здесь означает «маршрут официальный и бесплатный», а не
«можно не думать». Срок проверки условий (``terms_review_due_at``) всё
равно проставляется отдельно, и шлюз без него не пускает: перед
коммерческим запуском условия хранения и перепубликации нужно проверить по
каждому источнику заново.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ResearchSource
from ..models.enums import LicenseStatus

_READ = {
    "fetch": True,
    "store_raw": True,
    "transform": True,
    "display_derived": True,
    "redistribute_raw": False,
}
_MANUAL = {
    "fetch": False,
    "store_raw": True,
    "transform": True,
    "display_derived": True,
    "redistribute_raw": False,
}
_NONE = {
    "fetch": False,
    "store_raw": False,
    "transform": False,
    "display_derived": False,
    "redistribute_raw": False,
}


@dataclass(frozen=True, slots=True)
class SourceSpec:
    source_id: str
    name: str
    owner: str
    access_method: str
    license_status: LicenseStatus
    note: str
    base_url: str = ""
    extra_urls: tuple[str, ...] = ()
    catalogue_urls: tuple[str, ...] = ()
    auth_type: str = "none"
    expected_frequency: str = ""
    source_timezone: str = "Europe/Moscow"
    allowed_operations: dict = field(default_factory=lambda: dict(_NONE))
    feeds: dict[str, str] = field(default_factory=dict)
    rate_limit_policy: dict = field(default_factory=dict)
    terms_checked_at: datetime | None = None
    terms_review_due_at: datetime | None = None
    terms_url: str = ""
    datasets: tuple[str, ...] = ()


RUSSIA_FREE: tuple[SourceSpec, ...] = (
    SourceSpec(
        "cbr_data", "Bank of Russia Data Service", "Банк России", "api",
        LicenseStatus.APPROVED,
        "официальный REST с OpenAPI 3.0; автоматическое получение данных "
        "собственным кодом прямо предусмотрено. Внутренняя аналитика и "
        "производные показатели разрешены, перепродажа сырого массива — нет. "
        "При цитировании обязательна ссылка; условия могут быть изменены в "
        "одностороннем порядке, поэтому проверка назначена на ноябрь",
        base_url="https://www.cbr.ru/dataservice/",
        catalogue_urls=("https://www.cbr.ru/dataservice/publications",),
        expected_frequency="monthly",
        allowed_operations=dict(_READ),
        feeds={"DEMAND": "full", "CAPACITY": "partial", "SPREAD": "partial"},
        terms_url="https://www.cbr.ru/user_agreement/",
        terms_checked_at=datetime(2026, 8, 1, tzinfo=UTC),
        terms_review_due_at=datetime(2026, 11, 1, tzinfo=UTC),
        rate_limit_policy={
            "strategy": "token_bucket",
            "requests_per_second": 1,
            "burst": 2,
        },
    ),
    SourceSpec(
        "cbr_legacy", "Веб-сервисы ЦБ (курсы, ставки)", "Банк России", "api",
        LicenseStatus.APPROVED,
        "REST/XML/SOAP: курсы, ключевая ставка, RUONIA, металлы",
        base_url="https://www.cbr.ru/scripts/",
        expected_frequency="daily",
        allowed_operations=dict(_READ),
        feeds={"SPREAD": "full"},
    ),
    SourceSpec(
        "fns_open_data", "FNS Open Data — Entity Metrics", "ФНС России", "file",
        LicenseStatus.APPROVED,
        "открытые данные используются без договора и лицензий, в том числе "
        "коммерчески. Обязательны ссылка на ФНС, законная цель и достоверное "
        "представление. Перепубликация сырого массива отключена решением "
        "продукта, хотя условия её допускают",
        base_url="https://file.nalog.ru/opendata/reestrod.csv",
        extra_urls=(
            "https://www.nalog.gov.ru/opendata/",
            "https://data.nalog.ru/opendata/",
        ),
        catalogue_urls=("https://www.nalog.gov.ru/opendata/",),
        expected_frequency="yearly",
        allowed_operations=dict(_READ),
        feeds={"SUPPLIER": "partial", "WCQ": "partial"},
        terms_url="https://www.nalog.gov.ru/files/tipovye_usloviya_od.pdf",
        terms_checked_at=datetime(2026, 8, 1, tzinfo=UTC),
        terms_review_due_at=datetime(2027, 2, 1, tzinfo=UTC),
        datasets=(
            "revenue_expenses",
            "headcount",
            "paid_taxes",
            "tax_debt",
        ),
    ),
    SourceSpec(
        "eis_open_data", "Открытые данные ЕИС закупок", "ЕИС", "file",
        LicenseStatus.APPROVED,
        "официальные выгрузки: извещения, контракты, исполнение, оплата, "
        "приёмка. HTML-поиск как массовый API не использовать. Следующий "
        "приоритет подключения: без него SUPPLIER видит финансы поставщика, "
        "но не заказы, ради которых движок и существует",
        expected_frequency="daily",
        allowed_operations=dict(_READ),
        feeds={"BUDGET": "full", "SUPPLIER": "full"},
    ),
    SourceSpec(
        "trudvsem", "API «Работа России»", "Роструд", "api",
        LicenseStatus.APPROVED,
        "бесплатный официальный API и выгрузки вакансий — законная замена "
        "hh.ru, условия которого ограничивают применение тематикой найма",
        expected_frequency="daily",
        allowed_operations=dict(_READ),
        feeds={"HIRING": "full"},
    ),
    SourceSpec(
        "rosstat", "Росстат / ЕМИСС", "Росстат", "file",
        LicenseStatus.APPROVED,
        "официальные открытые CSV/XLSX/ZIP: производство, запасы, розница и "
        "цены производителей. Открытые данные разрешено использовать и "
        "перерабатывать при сохранении достоверности и ссылки на источник; "
        "сырой массив продукт не перераспространяет",
        base_url="https://rosstat.gov.ru/",
        catalogue_urls=("https://rosstat.gov.ru/statistics/price",),
        expected_frequency="monthly",
        allowed_operations=dict(_READ),
        feeds={"DEMAND": "full", "SPREAD": "full", "CAPACITY": "full"},
        terms_url="https://rosstat.gov.ru/opendata/",
        terms_checked_at=datetime(2026, 8, 15, tzinfo=UTC),
        terms_review_due_at=datetime(2027, 2, 15, tzinfo=UTC),
        datasets=("producer_prices",),
    ),
    SourceSpec(
        "budget_open_data", "Открытые данные «Электронного бюджета»",
        "Казначейство России", "file",
        LicenseStatus.APPROVED,
        "официальные наборы по бюджетам и исполнению; связывается с "
        "контрактами ЕИС. Пресс-релиз о бюджете фактом расходования не "
        "является",
        expected_frequency="monthly",
        allowed_operations=dict(_READ),
        feeds={"BUDGET": "full"},
    ),
    SourceSpec(
        "issuer_ir", "IR-разделы сайтов эмитентов", "эмитенты", "html",
        LicenseStatus.MANUAL_ONLY,
        "PDF/XLSX/RSS по allowlist каждого эмитента; сохранять оригинал "
        "документа и время публикации. До заполнения allowlist — ручной "
        "импорт официального файла",
        expected_frequency="daily",
        allowed_operations=dict(_MANUAL),
        feeds={"WCQ": "full"},
    ),
    SourceSpec(
        "broker_quotes", "Котировки через API брокера", "Т-Инвестиции", "api",
        LicenseStatus.APPROVED,
        "свечи, стаканы и поток котировок по токену брокерского счёта — "
        "законный путь к российским ценам в обход ограничений ISS. Токен на "
        "чтение у приложения уже есть",
        auth_type="api_key",
        expected_frequency="daily",
        allowed_operations=dict(_READ),
        feeds={},
    ),
    SourceSpec(
        "worldbank_pinksheet", "World Bank Pink Sheet", "World Bank", "file",
        LicenseStatus.APPROVED,
        "ежемесячный XLSX: нефть, газ, уголь, металлы, агросырьё — мировая "
        "нога сырьевого спреда",
        source_timezone="UTC",
        expected_frequency="monthly",
        allowed_operations=dict(_READ),
        feeds={"SPREAD": "full"},
    ),
    SourceSpec(
        "eia_open_data", "EIA Open Data", "U.S. EIA", "api",
        LicenseStatus.APPROVED,
        "бесплатный API по энергетике",
        source_timezone="UTC",
        expected_frequency="weekly",
        allowed_operations=dict(_READ),
        feeds={"SPREAD": "full"},
    ),
)

RUSSIA_MANUAL: tuple[SourceSpec, ...] = (
    SourceSpec(
        "fns_bfo", "БФО ФНС (полная отчётность)", "ФНС России", "api",
        LicenseStatus.REQUIRES_CONTRACT,
        "просмотр и скачивание отчётности одной компании бесплатны, REST API "
        "платный. Замена: открытые наборы ФНС плюс ручная загрузка полной "
        "отчётности по важным компаниям",
        auth_type="api_key",
        expected_frequency="yearly",
        allowed_operations=dict(_NONE),
        feeds={"WCQ": "partial"},
    ),
    SourceSpec(
        "interfax_disclosure", "Интерфакс e-disclosure", "Интерфакс", "html",
        LicenseStatus.MANUAL_ONLY,
        "публичный просмотр бесплатен, API и FTP платные. Замена: IR-сайты "
        "эмитентов и ручной импорт",
        expected_frequency="daily",
        allowed_operations=dict(_MANUAL),
        feeds={"WCQ": "full"},
    ),
    SourceSpec(
        "fedresurs", "Федресурс", "Интерфакс", "api",
        LicenseStatus.MANUAL_ONLY,
        "карточки публичны, автоматический сервис по договору. Проверять "
        "вручную только сильные гипотезы",
        auth_type="api_key",
        expected_frequency="daily",
        allowed_operations=dict(_MANUAL),
        feeds={"SUPPLIER": "full"},
    ),
    SourceSpec(
        "yandex_wordstat", "Яндекс Wordstat", "Яндекс", "api",
        LicenseStatus.MANUAL_ONLY,
        "веб-интерфейс бесплатен, API перенесён в платный Yandex Search API. "
        "Замена: ручная еженедельная выгрузка выбранных запросов",
        auth_type="oauth",
        expected_frequency="weekly",
        allowed_operations=dict(_MANUAL),
        feeds={"DEMAND": "full"},
    ),
    SourceSpec(
        "sberindex", "СберИндекс", "Сбербанк", "html",
        LicenseStatus.MANUAL_ONLY,
        "публичные исследования доступны, документированного бесплатного "
        "production API нет. Ручной импорт как подтверждающий источник",
        expected_frequency="monthly",
        allowed_operations=dict(_MANUAL),
        feeds={"DEMAND": "full"},
    ),
    SourceSpec(
        "moex_iss", "Московская биржа ISS", "MOEX", "api",
        LicenseStatus.PROHIBITED,
        "данные с задержкой доступны без подписки, но биржа прямо запрещает "
        "использовать их для извлечения прибыли без договора. Замена: API "
        "брокера. Торговый контур приложения — отдельное решение владельца, "
        "на исследовательский контур оно не распространяется",
        base_url="https://iss.moex.com/",
        expected_frequency="daily",
        allowed_operations=dict(_NONE),
        feeds={},
    ),
    SourceSpec(
        "hh_ru", "hh.ru", "HeadHunter", "api",
        LicenseStatus.PROHIBITED,
        "условия API ограничивают использование тематикой найма и "
        "трудоустройства, инвестиционная аналитика в неё не входит. Замена: "
        "API «Работа России» и карьерные страницы компаний",
        auth_type="api_key",
        expected_frequency="monthly",
        allowed_operations=dict(_NONE),
        feeds={"HIRING": "full"},
    ),
)

CRYPTO: tuple[SourceSpec, ...] = (
    SourceSpec(
        "defillama", "DefiLlama API", "DefiLlama", "api",
        LicenseStatus.APPROVED,
        "без ключа и авторизации: TVL, fees, revenue, объёмы DEX, stablecoins, "
        "yields. Разблокировки токенов в бесплатный план не входят — их "
        "считаем сами из документации, vesting-контрактов и фактических "
        "переводов on-chain",
        base_url="https://api.llama.fi/",
        source_timezone="UTC",
        expected_frequency="daily",
        allowed_operations=dict(_READ),
        feeds={"CRYPTO_ECON": "full"},
    ),
    SourceSpec(
        "etherscan", "Etherscan API V2", "Etherscan", "api",
        LicenseStatus.APPROVED,
        "бесплатный ключ: 3 запроса в секунду и до 100 000 в сутки на "
        "выбранных сетях",
        auth_type="api_key",
        source_timezone="UTC",
        expected_frequency="hourly",
        allowed_operations=dict(_READ),
        feeds={"CRYPTO_SUPPLY": "full", "CRYPTO_ECON": "full"},
        rate_limit_policy={
            "strategy": "token_bucket",
            "requests_per_second": 3,
            "daily_quota": 100_000,
        },
    ),
    SourceSpec(
        "github", "GitHub REST API", "GitHub", "api",
        LicenseStatus.APPROVED,
        "60 запросов в час без авторизации, 5000 с бесплатным токеном; "
        "учитывать форки, ботов и зеркала",
        auth_type="api_key",
        source_timezone="UTC",
        expected_frequency="daily",
        allowed_operations=dict(_READ),
        feeds={"CRYPTO_ECON": "full"},
        rate_limit_policy={"strategy": "fixed_window", "requests_per_hour": 5000},
    ),
    SourceSpec(
        "coingecko", "CoinGecko Demo API", "CoinGecko", "api",
        LicenseStatus.APPROVED,
        "бесплатно: 100 запросов в минуту, 10 000 в месяц. Тикер "
        "идентификатором актива не является",
        auth_type="api_key",
        source_timezone="UTC",
        expected_frequency="daily",
        allowed_operations=dict(_READ),
        feeds={"CRYPTO_SUPPLY": "full"},
        rate_limit_policy={
            "strategy": "token_bucket",
            "requests_per_minute": 100,
            "monthly_quota": 10_000,
        },
    ),
    SourceSpec(
        "binance_market", "Binance Market Data API", "Binance", "api",
        LicenseStatus.APPROVED,
        "публичные market-data endpoints не требуют ключа: свечи, сделки, "
        "стаканы, объёмы",
        base_url="https://api.binance.com/",
        source_timezone="UTC",
        expected_frequency="daily",
        allowed_operations=dict(_READ),
        feeds={},
    ),
    SourceSpec(
        "project_docs", "Документация и контракты проектов", "проекты", "html",
        LicenseStatus.MANUAL_ONLY,
        "токеномика, казначейство, vesting, governance, value capture — по "
        "allowlist, каждое изменение новой версией. Именно отсюда, а не из "
        "агрегатора, берутся графики разблокировок",
        source_timezone="UTC",
        expected_frequency="weekly",
        allowed_operations=dict(_MANUAL),
        feeds={"CRYPTO_SUPPLY": "full"},
    ),
    SourceSpec(
        "dune", "Dune Analytics", "Dune", "api",
        LicenseStatus.REQUIRES_CONTRACT,
        "бесплатный план — 2500 credits в месяц; использовать только для "
        "метрик, которые нельзя посчитать проще. Нужен ключ, версия запроса "
        "и хеш SQL",
        auth_type="api_key",
        source_timezone="UTC",
        expected_frequency="daily",
        allowed_operations=dict(_READ),
        feeds={"CRYPTO_ECON": "full"},
    ),
)

ALL_SOURCES: tuple[SourceSpec, ...] = RUSSIA_FREE + RUSSIA_MANUAL + CRYPTO

FLAGS: dict[str, str] = {
    "fns_open_data": "ENABLE_FNS_OPEN_DATA",
    "fns_bfo": "ENABLE_FNS_BFO",
}

FLAG_DEFAULTS: dict[str, bool] = {
    "ENABLE_FNS_OPEN_DATA": True,
    "ENABLE_FNS_BFO": False,
}


def enabled_by_flag(source_id: str) -> bool:
    """Разрешён ли источник выключателем окружения."""
    import os

    name = FLAGS.get(source_id)
    if name is None:
        return True
    raw = os.environ.get(name)
    if raw is None:
        return FLAG_DEFAULTS.get(name, False)
    return raw.strip().lower() in ("1", "true", "yes", "on")


CONNECT_ORDER: tuple[str, ...] = (
    "cbr_data",
    "fns_open_data",
    "eis_open_data",
    "trudvsem",
    "rosstat",
    "budget_open_data",
    "broker_quotes",
    "defillama",
    "etherscan",
    "github",
    "coingecko",
    "dune",
)


def sync_registry(
    session: Session, *, now: datetime | None = None
) -> tuple[int, int]:
    """Записать реестр в базу. Возвращает (добавлено, обновлено).

    Правовой статус всегда переписывается из кода: реестр — это
    зафиксированное решение о праве, и расхождение между репозиторием и
    базой здесь самый неприятный вид расхождения. Включение источника —
    изменение этого файла вместе с проверкой условий, а не UPDATE.

    ``terms_review_due_at`` переносится из реестра, где он означает
    состоявшуюся проверку условий. У источника без проверки поля нет, и шлюз
    трактует это как отказ: ``approved`` без даты — мнение, а не решение.
    """
    moment = now or datetime.now(UTC)
    known = {
        row.source_id: row
        for row in session.execute(select(ResearchSource)).scalars()
    }
    added = updated = 0
    for spec in ALL_SOURCES:
        row = known.get(spec.source_id)
        if row is None:
            row = ResearchSource(source_id=spec.source_id)
            session.add(row)
            added += 1
        else:
            updated += 1
        row.name = spec.name
        row.owner = spec.owner
        row.base_url = spec.base_url
        row.access_method = spec.access_method
        row.auth_type = spec.auth_type
        row.license_status = spec.license_status
        row.allowed_operations = dict(spec.allowed_operations)
        row.rate_limit_policy = dict(spec.rate_limit_policy)
        row.note = spec.note
        row.expected_frequency = spec.expected_frequency
        row.source_timezone = spec.source_timezone
        row.terms_url = spec.terms_url
        row.terms_checked_at = spec.terms_checked_at
        row.terms_review_due_at = spec.terms_review_due_at
        row.enabled = (
            spec.license_status is LicenseStatus.APPROVED
            and spec.terms_review_due_at is not None
            and enabled_by_flag(spec.source_id)
        )
    session.flush()
    return added, updated


def readiness(session: Session) -> dict:
    """Готовность контура: что разрешено, что ждёт проверки, чего не будет."""
    rows = list(session.execute(select(ResearchSource)).scalars())
    free = [r for r in rows if r.license_status is LicenseStatus.APPROVED]
    awaiting = [r for r in free if r.terms_review_due_at is None]
    return {
        "total": len(rows),
        "free_route": len(free),
        "awaiting_terms_check": [r.source_id for r in awaiting],
        "manual_only": [
            r.source_id for r in rows if r.license_status is LicenseStatus.MANUAL_ONLY
        ],
        "paid_or_prohibited": [
            {
                "source_id": r.source_id,
                "name": r.name,
                "status": str(r.license_status),
                "note": r.note,
            }
            for r in rows
            if r.license_status
            in (LicenseStatus.REQUIRES_CONTRACT, LicenseStatus.PROHIBITED)
        ],
        "connect_order": list(CONNECT_ORDER),
    }


__all__ = [
    "ALL_SOURCES",
    "CONNECT_ORDER",
    "CRYPTO",
    "RUSSIA_FREE",
    "RUSSIA_MANUAL",
    "SourceSpec",
    "readiness",
    "sync_registry",
]

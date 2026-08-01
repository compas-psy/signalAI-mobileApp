"""Происхождение факта: три разные вещи, которые нельзя смешивать (§2.4).

Смешение было и стоило дорого. Коллектор ставил `lineage_root_id = source_id`,
и из этого следовал вывод «два источника — максимум два корня, правило 3–2–1
недостижимо». Вывод неверен, а неверен он потому, что источник и корень
отвечают на разные вопросы.

**source_id** — откуда пришли байты и по какому праву. Транспорт и правовой
режим. Зеркало того же набора на другом хосте — тот же набор.

**lineage_root_id** — первичный информационный корень: то, из чего факт
возник. Опрос предприятий и реестр контрактов — разные корни, даже если
пришли одним запросом с одного хоста. Две строки одного опроса — один
корень, даже если они за разные периоды.

**independence_group** — способ формирования данных. Опрос, административный
учёт, рыночная сделка, отчётность — разные способы. Четыре набора ФНС имеют
разные корни, но один способ: все они собраны налоговым администрированием,
и считать их четырьмя независимыми подтверждениями значит подтверждать
показание свидетеля его же словами.

Корень обязан быть детерминированным. Случайный идентификатор на строку или
на запуск превращает повторную загрузку в независимое подтверждение — то
есть ломает ровно то правило, ради которого всё это существует.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Способы формирования данных. Не список источников, а список методов:
#: две организации, собирающие данные одинаково, независимости не дают.
SURVEY = "survey"  # опрос: мониторинг предприятий, деловые ожидания
ADMIN = "administrative"  # административный учёт: налоги, регистрации
CONTRACT = "contract"  # хозяйственный договор: закупки, исполнение
MARKET = "market"  # рыночная сделка: котировки, объёмы
STATISTICAL = "statistical"  # статистическое наблюдение: Росстат
DISCLOSURE = "disclosure"  # раскрытие эмитента: отчётность, сообщения
ONCHAIN = "onchain"  # данные сети: транзакции, контракты


@dataclass(frozen=True, slots=True)
class Provenance:
    """Откуда факт, из чего он возник и как получен."""

    source_id: str
    lineage_root_id: str
    independence_group: str


# ── Банк России ─────────────────────────────────────────────────────────────
#
# Разные наборы — разные корни: ключевая ставка и опрос предприятий
# рождаются по-разному. Строки и периоды одного набора корень не множат:
# июль и август одного опроса — это один и тот же способ узнать одно и то
# же, растянутый во времени.
_CBR_GROUPS = {
    "enterprise_monitoring": SURVEY,
    "key_rate": ADMIN,
    "fx_usd_rub": MARKET,
    "industry_indicators": STATISTICAL,
}


def cbr(dataset: str) -> Provenance:
    return Provenance(
        source_id="cbr_data",
        lineage_root_id=f"cbr:{dataset}",
        independence_group=_CBR_GROUPS.get(dataset, ADMIN),
    )


# ── ФНС ─────────────────────────────────────────────────────────────────────
#
# Четыре набора — четыре корня: доходы, численность, уплаченные налоги и
# задолженность возникают из разных первичных документов. Но способ у всех
# один — налоговое администрирование, — и в правиле независимости они
# считаются одним свидетелем.
def fns(dataset: str) -> Provenance:
    return Provenance(
        source_id="fns_open_data",
        lineage_root_id=f"fns:{dataset}",
        independence_group=ADMIN,
    )


# ── ЕИС ─────────────────────────────────────────────────────────────────────
#
# Корень — контракт, а не выгрузка и не строка. Извещение, заключение,
# аванс, приёмка, оплата, изменения и отмена — этапы жизни одного и того же
# контракта. Считать их независимыми подтверждениями значит подтверждать
# факт его же собственными последствиями.
def eis_contract(contract_id: str, supplier_group: str = "") -> Provenance:
    return Provenance(
        source_id="eis_open_data",
        lineage_root_id=f"eis:contract:{contract_id}",
        # Независимость даёт не другой контракт, а другая экономическая
        # группа поставщиков: два контракта одного холдинга — одно решение.
        independence_group=f"{CONTRACT}:{supplier_group}" if supplier_group else CONTRACT,
    )


def rosstat(dataset: str) -> Provenance:
    return Provenance(
        source_id="rosstat",
        lineage_root_id=f"rosstat:{dataset}",
        independence_group=STATISTICAL,
    )


def issuer_disclosure(entity_id: str, kind: str = "report") -> Provenance:
    return Provenance(
        source_id="issuer_ir",
        lineage_root_id=f"ir:{entity_id}:{kind}",
        independence_group=DISCLOSURE,
    )


def independent_roots(items) -> int:
    """Сколько по-настоящему независимых корней в наборе доказательств.

    Считаются пары «корень + способ». Два корня одного способа независимыми
    не считаются: четыре набора ФНС дают четыре корня и одно налоговое
    администрирование, и подтверждение здесь мнимое.
    """
    groups: dict[str, set[str]] = {}
    for item in items:
        group = getattr(item, "independence_group", "") or ADMIN
        root = getattr(item, "lineage_root_id", "")
        if root:
            groups.setdefault(group, set()).add(root)
    # По одному зачёту на способ: внутри способа корни дублируют друг друга.
    return len(groups)


def distinct_roots(items) -> int:
    """Сколько различных корней. Мера охвата, а не независимости."""
    return len({getattr(i, "lineage_root_id", "") for i in items if
                getattr(i, "lineage_root_id", "")})


__all__ = [
    "ADMIN",
    "CONTRACT",
    "DISCLOSURE",
    "MARKET",
    "ONCHAIN",
    "Provenance",
    "STATISTICAL",
    "SURVEY",
    "cbr",
    "distinct_roots",
    "eis_contract",
    "fns",
    "independent_roots",
    "issuer_disclosure",
    "rosstat",
]

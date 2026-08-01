"""Связь отраслевого наблюдения с бумагой (§9, §13.2).

Спрос Банк России измеряет по разделам ОКВЭД, а гипотеза адресуется
эмитенту. Без этой связи движок считает, шлюз пригодности отвергает, и
экран пуст при полностью исправном сборе — то есть самый дорогой вид
пустоты: необъяснимый.

Отдельно проверяется, что реестр не притворяется знающим больше, чем
знает. Ошибка в ИНН молча припишет эмитенту чужую отчётность, и заметить
это будет негде.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.models import ResearchObservation
from app.research import issuers, run_engines
from app.research.entities import MIN_AUTOMATIC

NOW = datetime(2026, 8, 1, tzinfo=UTC)


def test_отрасль_связывает_наблюдение_с_бумагами():
    """Ровно то звено, которого не было."""
    металлурги = issuers.in_section("C")
    assert {i.secid for i in металлурги} >= {"CHMF", "NLMK", "MAGN"}
    assert all(i.section == "C" for i in металлурги)


def test_раздел_читается_и_из_кода_с_подробностями():
    """ЦБ пишет раздел по-разному, а бумага одна и та же."""
    assert [i.secid for i in issuers.in_section("c")] == [
        i.secid for i in issuers.in_section("C")
    ]
    assert issuers.in_section("") == []


def test_бумага_находится_и_по_полному_идентификатору():
    assert issuers.of("MOEX:EQ:SBER").name == "Сбербанк"
    assert issuers.of("SBER").section == "K"
    assert issuers.of("НЕТ-ТАКОЙ") is None


def test_неизвестный_инн_это_не_пустой_инн():
    """Разница решает, можно ли брать по эмитенту выгрузки ФНС.

    Они ключуются по ИНН; подставить чужой — приписать эмитенту чужую
    отчётность.
    """
    сбер = issuers.of("SBER")
    втб = issuers.of("VTBR")
    assert сбер.usable_for_financials is True
    assert втб.usable_for_financials is False
    assert втб.inn == ""


def test_уверенность_привязки_падает_без_проверенного_инн():
    """Отраслевой принадлежности хватает движку спроса и не хватает
    ничему, что требует отчётности юридического лица."""
    assert issuers.of("SBER").confidence > issuers.of("VTBR").confidence
    # Обе всё же выше порога автоматики: отрасль — публично известный факт.
    assert issuers.of("VTBR").confidence >= MIN_AUTOMATIC


def test_реестр_честно_отчитывается_о_дырах():
    """«Эмитент не найден» и «найден, но ИНН неизвестен» требуют разного,
    а выглядят одинаково отсутствием гипотезы."""
    покрытие = issuers.coverage()
    assert покрытие["issuers"] == len(issuers.REGISTRY)
    assert покрытие["with_inn"] >= 2
    assert "VTBR" in покрытие["without_inn"]


def test_каждый_раздел_реестра_известен():
    """Раздел вне классификатора связал бы наблюдение ни с чем."""
    for issuer in issuers.REGISTRY:
        assert issuer.section in issuers.SECTIONS, issuer.secid
        assert issuer.sector_name


# ── Наблюдения → ряд периодов ───────────────────────────────────────────────


def факт(period: date, value: str, section: str = "C") -> ResearchObservation:
    return ResearchObservation(
        observation_type=f"{run_engines.DEMAND_PREFIX}Спрос",
        entity_id=section,
        source_id="cbr_data",
        period_end=period,
        first_seen_at=NOW,
        tradable_at=NOW,
        lineage_root_id="cbr_data",
        source_locator={},
        value_numeric=Decimal(value),
    )


def test_порядок_периодов_хронологический():
    """Перепутанный порядок даст рост вместо падения без единой ошибки в
    расчёте — движок сравнивает последний период с прошлогодним."""
    rows = [
        факт(date(2026, 6, 30), "3"),
        факт(date(2025, 6, 30), "1"),
        факт(date(2026, 3, 31), "2"),
    ]
    periods = run_engines.demand_periods(rows)
    assert [p.period for p in periods] == [
        "2025-06-30", "2026-03-31", "2026-06-30",
    ]


def test_неизмеренное_значение_не_становится_нулём():
    """«Не измерено» и «ноль» — разные факты."""
    пустой = факт(date(2026, 6, 30), "0")
    пустой.value_numeric = None
    assert run_engines.demand_periods([пустой]) == []


def test_короткого_ряда_не_хватает_и_это_сказано(session):
    """Движок промолчал бы; отчёт обязан назвать причину."""
    from app.research.sources import sync_registry

    sync_registry(session, now=NOW)
    session.add(факт(date(2026, 6, 30), "3"))
    session.flush()
    report = run_engines.run_demand(session, now=NOW)

    assert report.observations == 1
    assert report.hypotheses == 0
    assert any("периодов 1" in note for note in report.skipped)


def test_без_наблюдений_это_результат_а_не_ошибка(session):
    report = run_engines.run_demand(session, now=NOW)
    assert report.observations == 0
    assert report.skipped
    assert "нет" in report.skipped[0]


def test_отчёт_движков_называет_числа():
    report = run_engines.EngineReport(
        observations=40, sections=3, signals=9, hypotheses=2
    )
    текст = report.summary()
    assert "наблюдений 40" in текст
    assert "гипотез 2" in текст


# ── Критик перед подтверждением ─────────────────────────────────────────────


def test_недоступный_критик_не_подтверждает_гипотезу(session):
    """Молчание модели — отсутствие проверки, а не её успешное прохождение.

    Ошибка здесь тихая и дорогая: недоступный критик молча превращал бы
    каждую гипотезу в подтверждённую.
    """
    from app.models.enums import HypothesisState
    from app.research.pipeline import RunReport, _apply_critic

    class Гипотеза:
        state = HypothesisState.CONFIRMED
        state_reason = ""

    fused = Гипотеза()
    report = RunReport()

    def падает(*_):
        raise RuntimeError("модель не отвечает")

    _apply_critic(падает, fused, [], report)

    assert fused.state is HypothesisState.EARLY_CANDIDATE
    assert "критик недоступен" in fused.state_reason
    assert report.notes


def test_критика_не_спрашивают_ниже_подтверждения(session):
    """Ниже подтверждения гипотеза ничего не обещает — вызов не окупается."""
    from app.models.enums import HypothesisState
    from app.research.pipeline import RunReport, _apply_critic

    class Гипотеза:
        state = HypothesisState.EARLY_CANDIDATE
        state_reason = "исходная причина"

    спрошен: list[int] = []
    fused = Гипотеза()
    _apply_critic(lambda *_: спрошен.append(1), fused, [], RunReport())

    assert not спрошен
    assert fused.state_reason == "исходная причина"


def test_отказ_критика_опускает_состояние_с_причиной(session):
    """Необъяснимое понижение на экране хуже, чем отсутствие гипотезы."""
    from app.models.enums import HypothesisState
    from app.research.pipeline import RunReport, _apply_critic

    class Вердикт:
        blocks_confirmation = True
        summary = "причинная цепочка неполна"

    class Гипотеза:
        state = HypothesisState.CONFIRMED
        state_reason = ""

    fused = Гипотеза()
    _apply_critic(lambda *_: Вердикт(), fused, [], RunReport())

    assert fused.state is HypothesisState.EARLY_CANDIDATE
    assert fused.state_reason == "причинная цепочка неполна"


# ── Сектор брокера вместо биржи ─────────────────────────────────────────────


def test_сектор_брокера_переводится_в_раздел_оквэд():
    """Брокер и Банк России говорят на разных языках.

    Без сопоставления «financial» и «K» — два несвязанных слова, и
    наблюдение снова не к чему привязать.
    """
    assert issuers.section_of_sector("financial") == "K"
    assert issuers.section_of_sector("ENERGY") == "B"
    assert issuers.section_of_sector(" utilities ") == "D"


def test_неизвестный_сектор_не_сваливается_в_прочее():
    """Приписать бумаге чужую отрасль хуже, чем не дать ей сигнала."""
    assert issuers.section_of_sector("нечто новое") == ""
    assert issuers.section_of_sector("") == ""


def test_справочник_брокера_даёт_эмитентов():
    rows = [
        {"ticker": "sber", "name": "Сбербанк", "sector": "financial"},
        {"ticker": "XXXX", "name": "Без сектора", "sector": "неизвестно"},
        {"ticker": "", "name": "Без тикера", "sector": "energy"},
    ]
    собрано = issuers.from_broker(rows)
    assert [i.secid for i in собрано] == ["SBER"]
    assert собрано[0].section == "K"


def test_инн_из_брокера_не_выдумывается():
    """Брокер ИНН не отдаёт, а выгрузки ФНС ключуются по нему."""
    новый = issuers.from_broker(
        [{"ticker": "NEWW", "name": "Новая", "sector": "materials"}]
    )[0]
    assert новый.inn == ""
    assert новый.usable_for_financials is False
    # А у известного эмитента проверенный ИНН сохраняется.
    сбер = issuers.from_broker(
        [{"ticker": "SBER", "name": "Сбербанк", "sector": "financial"}]
    )[0]
    assert сбер.inn == "7707083893"

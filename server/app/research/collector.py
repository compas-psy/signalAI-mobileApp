"""Сбор: от разрешения до наблюдения в базе (§10.1, §8.2).

До сих пор контур был собран из частей, но нигде не соединён. Реестр знал,
что можно; адаптеры умели разбирать; движки умели считать; таблица
наблюдений существовала. Записи в неё не делал никто, и экран разведки был
пуст не потому, что рынок спокоен, а потому что цепочка обрывалась здесь.

Что этот модуль делает и чего не делает. Он получает пропуск, забирает то,
что нашёл адаптер, превращает разобранные значения в наблюдения и пишет их
с полной цепочкой происхождения. Он не решает, что важно: смысл чисел —
дело движков, и смешение слоёв означало бы, что причину странной гипотезы
придётся искать в разборе XML.

Про повторный сбор. Наблюдение опознаётся по источнику, объекту, типу и
периоду. Повторная загрузка того же файла не должна плодить копии: три
одинаковых факта в правиле 3–2–1 выглядели бы как три независимых
подтверждения, а это ровно та ошибка, ради предотвращения которой правило
и существует.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ResearchObservation
from .adapters import cbr, fns
from .codes import observation_code
from .collect import Fetched
from .policy import CollectionDenied, Permit, authorize
from .provenance import Provenance, cbr as cbr_provenance, fns as fns_provenance
from .reach import USER_AGENT
from .timeline import tradable_at

#: Сколько ждать ответа на обычный (не файловый) запрос.
TIMEOUT = 30.0


@dataclass
class CollectReport:
    """Что вышло из одного прогона сбора."""

    attempted: int = 0
    fetched: int = 0
    written: int = 0
    duplicates: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"забрано {self.fetched}/{self.attempted}", f"наблюдений {self.written}"]
        if self.duplicates:
            parts.append(f"повторов {self.duplicates}")
        if self.skipped:
            parts.append(f"пропущено {len(self.skipped)}")
        if self.errors:
            parts.append(f"ошибок {len(self.errors)}: {self.errors[0]}")
        return ", ".join(parts)


def collect_cbr(session: Session, *, now: datetime | None = None) -> CollectReport:
    """Забрать показатели Банка России.

    Начинается с публикаций: у сервиса своя нумерация, и подставлять в
    запрос наше имя набора бессмысленно — живой прогон отвечал на это 501.
    """
    moment = now or datetime.now(UTC)
    report = CollectReport()
    try:
        permit = authorize(session, cbr.SOURCE_ID, {"fetch", "transform"}, now=moment)
    except CollectionDenied as denied:
        report.skipped.append(f"{cbr.SOURCE_ID}: {denied.reason}")
        return report

    # Двухшаговый сбор: сначала дерево публикаций, из него — числовые
    # идентификаторы наших наборов, и только потом сами ряды. Прежний
    # проход останавливался на первом шаге: список публикаций забирался,
    # разбирался в ноль строк, и «сбор идёт» означало «сбор не приносит
    # ничего». Подставлять в запрос наши имена наборов бессмысленно — у
    # сервиса своя нумерация, что живой прогон и показал (501 на все).
    план = cbr.fetch_plan(permit)
    report.attempted += 1
    публикации = _get(план[0].url, moment)
    if публикации is None or not публикации.ok:
        report.errors.append(f"{план[0].url}: нет ответа")
        return report
    report.fetched += 1
    найдено = cbr.match_publications(публикации.body)
    if not найдено:
        report.errors.append(
            "в дереве публикаций не нашлось ни одного нашего набора — "
            "проверить ключевые слова по живому ответу"
        )
        return report

    targets = [
        (dataset, cbr.datasets_of(pub_id))
        for dataset, ids in найдено.items()
        for pub_id in ids[:3]
    ]
    for dataset, url in targets:
        report.attempted += 1
        fetched = _get(url, moment)
        if fetched is None or not fetched.ok:
            report.errors.append(f"{url}: нет ответа")
            continue
        report.fetched += 1
        rows = cbr.parse(fetched, dataset=dataset)
        if not rows:
            # Пустой разбор — новость, а не тишина: формат ответа мог
            # оказаться не тем, который ждёт разбор. Первые байты уходят в
            # отчёт, чтобы следующий шаг делался по факту, а не по догадке.
            report.errors.append(
                f"{dataset}: разбор дал 0 строк, ответ начинается с "
                f"{fetched.body[:120]!r}"
            )
            continue
        empty = 0
        for datum in rows:
            # Строка без числа и без периода — не факт, а описание набора:
            # сервис данных отдаёт их вперемешку со значениями. Записать её
            # наблюдением значит положить в правило 3–2–1 утверждение,
            # которого никто не делал, и вдобавок столкнуть все такие строки
            # в один дедуп-ключ (период у всех пустой).
            if datum.value is None and datum.period_end is None:
                empty += 1
                continue
            when = cbr.availability(datum, first_seen_at=moment)
            written = _write(
                session,
                source_id=cbr.SOURCE_ID,
                observation_type=observation_code(
                    "cbr", datum.dataset, datum.indicator
                ),
                entity_id=datum.industry or datum.region or "RU",
                value=datum.value,
                unit=datum.unit,
                period_start=datum.period_start,
                period_end=datum.period_end,
                published_at=datum.published_at,
                availability=when,
                first_seen_at=moment,
                locator={"url": url, "indicator": datum.indicator},
                raw_sha256=fetched.sha256,
                provenance=cbr_provenance(datum.dataset),
            )
            report.written += int(written)
            report.duplicates += int(not written)
        if empty:
            # Молчать нельзя: если пустыми окажутся все строки, «наблюдений
            # 0» будет выглядеть как спокойный рынок вместо неразобранного
            # формата.
            report.skipped.append(
                f"{dataset}: {empty} строк без значения и периода — описание "
                f"набора, а не факт"
            )
    return report


def collect_fns(
    session: Session, *, now: datetime | None = None, catalogue_html: str = ""
) -> CollectReport:
    """Забрать открытые наборы ФНС.

    Адреса читаются из паспортов: имя файла содержит дату выпуска и дату
    версии структуры, и собранный по шаблону адрес отвечает 404 — снаружи
    это неотличимо от «источник ничего не приносит».

    Сами выгрузки здесь не скачиваются: они по сто и двести мегабайт, и
    место им в потоковой загрузке, а не в шаге, который ходит по страницам.
    Этот проход устанавливает актуальные адреса и сохраняет их как факт.
    """
    moment = now or datetime.now(UTC)
    report = CollectReport()
    try:
        authorize(session, fns.SOURCE_ID, {"fetch"}, now=moment)
    except CollectionDenied as denied:
        report.skipped.append(f"{fns.SOURCE_ID}: {denied.reason}")
        return report

    каталог = catalogue_html or _text(fns.CATALOGUE)
    pages: dict[str, str] = {}
    for target in fns.passport_urls(catalogue_html=каталог):
        report.attempted += 1
        html = _text(target.url)
        if not html:
            report.errors.append(f"{target.url}: паспорт не прочитан")
            continue
        report.fetched += 1
        pages[target.kind] = html
    return _record_plan(session, report, pages, moment)


def _record_plan(
    session: Session, report: CollectReport, pages: dict[str, str], moment: datetime
) -> CollectReport:
    """Сохранить установленные адреса выгрузок как наблюдения о плане.

    Это не данные о компаниях, а факт о самом источнике: какая публикация
    актуальна сегодня. Без него следующий прогон снова пойдёт читать
    паспорта, а сравнить «изменилась ли публикация» будет не с чем.
    """
    for kind, html in pages.items():
        links = fns.links_from_passport(html, fns.PASSPORTS.get(kind, fns.CATALOGUE))
        if "data" not in links:
            report.errors.append(f"{kind}: в паспорте нет ссылки на выгрузку")
            continue
        written = _write(
            session,
            source_id=fns.SOURCE_ID,
            observation_type=observation_code("fns", "plan", kind),
            entity_id="fns_open_data",
            value=None,
            text=links["data"],
            unit="",
            period_start=None,
            period_end=None,
            published_at=None,
            availability=None,
            first_seen_at=moment,
            locator={"passport": fns.PASSPORTS.get(kind, ""), **links},
            raw_sha256="",
            provenance=fns_provenance(kind),
        )
        report.written += int(written)
        report.duplicates += int(not written)
    return report


def _write(
    session: Session,
    *,
    source_id: str,
    observation_type: str,
    entity_id: str,
    value: Decimal | None,
    unit: str,
    period_start,
    period_end,
    published_at: datetime | None,
    availability,
    first_seen_at: datetime,
    locator: dict,
    raw_sha256: str,
    provenance: Provenance,
    text: str = "",
) -> bool:
    """Записать наблюдение, если такого ещё нет.

    Возвращает `False` на повторе. Три копии одного факта в правиле 3–2–1
    выглядели бы как три независимых подтверждения — то есть ровно та
    ошибка, ради предотвращения которой правило и существует.
    """
    existing = session.execute(
        select(ResearchObservation.id).where(
            ResearchObservation.source_id == source_id,
            ResearchObservation.observation_type == observation_type,
            ResearchObservation.entity_id == entity_id,
            ResearchObservation.period_end == period_end,
        )
    ).first()
    if existing is not None:
        return False

    when = availability or _fallback_availability(published_at, first_seen_at)
    session.add(
        ResearchObservation(
            observation_type=observation_type,
            entity_id=entity_id,
            source_id=source_id,
            period_start=period_start,
            period_end=period_end,
            published_at=published_at,
            first_seen_at=first_seen_at,
            tradable_at=when.tradable_at,
            publication_time_uncertain=when.publication_time_uncertain,
            # Корень — не источник. Источник говорит, откуда байты и по
            # какому праву; корень — из чего факт возник. Приравняв их, мы
            # получили вывод «два источника — максимум два корня», и вывод
            # был неверен: у одного источника наборов несколько, и рождаются
            # они по-разному.
            lineage_root_id=provenance.lineage_root_id,
            source_locator=locator,
            raw_sha256=raw_sha256,
            value_numeric=value,
            value_text=text,
            unit=unit,
        )
    )
    return True


def _fallback_availability(published_at: datetime | None, first_seen_at: datetime):
    return tradable_at(published_at=published_at, first_seen_at=first_seen_at)


def _get(url: str, moment: datetime) -> Fetched | None:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return Fetched(
                url=url,
                status=response.status,
                body=response.read(),
                requested_at=moment,
                responded_at=datetime.now(UTC),
                content_type=response.headers.get("Content-Type", "") or "",
            )
    except urllib.error.HTTPError as error:
        return Fetched(
            url=url, status=error.code, body=b"",
            requested_at=moment, responded_at=datetime.now(UTC),
        )
    except Exception:  # noqa: BLE001 — недоступность видна отдельной пробой
        return None


def _text(url: str) -> str:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read(1024 * 1024).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def collect_all(session: Session, *, now: datetime | None = None) -> CollectReport:
    """Пройти по всем источникам, у которых есть написанный адаптер."""
    total = CollectReport()
    for one in (collect_cbr(session, now=now), collect_fns(session, now=now)):
        total.attempted += one.attempted
        total.fetched += one.fetched
        total.written += one.written
        total.duplicates += one.duplicates
        total.skipped.extend(one.skipped)
        total.errors.extend(one.errors)
    return total


__all__ = ["CollectReport", "collect_all", "collect_cbr", "collect_fns"]

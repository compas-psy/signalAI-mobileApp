"""Ранние сигналы: гипотезы и готовность источников (ТЗ Early Signals §18.2).

Что здесь есть и чего нет. Есть чтение гипотез и состояние источников. Нет
и не появится ничего, что отправляет заявку: §18.3 требует буквально «no
endpoint для выставления заявок», а §16.4 запрещает выдавать BUY, SELL,
гарантированную доходность и размер позиции. Гипотеза отвечает на вопрос
«что стоит изучить», а не «что купить».

Отдельный адрес готовности источников нужен по той же причине, по какой в
конвейере пакетов есть статус: пустая выдача сама по себе неинформативна.
«Гипотез нет, потому что рынок спокоен» и «гипотез нет, потому что ни один
источник не подключён» — разные новости, и вторая требует действия
владельца, а не ожидания.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import HypothesisEvidence, ResearchHypothesis, ResearchSource
from ...models.enums import HypothesisState
from ...research import adapters, explore, gateway, netdiag, reach
from ...research.adapters import cbr, fns
from ...research.engines import ENGINES
from ...research.policy import CollectionDenied, authorize
from ...research.sources import ALL_SOURCES, CONNECT_ORDER, readiness, sync_registry
from ...schemas.common import ApiModel, Money

router = APIRouter(prefix="/research", tags=["research"])


class EvidenceOut(ApiModel):
    role: str
    claim: str = ""
    lineage_root_id: str = ""
    independence_group: str = ""
    confidence: Money = Decimal(1)


class HypothesisOut(ApiModel):
    """Гипотеза как самостоятельный объект аудита (§16.1).

    Поля ``executable``, ``side`` и ``quantity`` отсутствуют намеренно и
    добавлены быть не могут: их нечему было бы заполнять.
    """

    id: str
    version: int
    market: str
    entity_id: str
    instrument_id: str = ""
    symbol: str = ""
    title: str
    direction: str
    state: str
    state_label: str
    as_of: datetime
    expected_lag: str = ""

    evidence_score: Money = Decimal(0)
    economic_score: Money = Decimal(0)
    market_context_score: Money | None = None
    market_context_state: str = "unknown"
    research_priority: Money = Decimal(0)

    three_two_one: dict = Field(default_factory=dict)
    target_kpis: list = Field(default_factory=list)
    causal_path: dict = Field(default_factory=dict)
    fact_summary: list = Field(default_factory=list)
    alternative_explanations: list = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    falsifiers: list = Field(default_factory=list)
    missing_evidence: list = Field(default_factory=list)
    evidence: list[EvidenceOut] = Field(default_factory=list)

    state_reason: str = ""
    next_review_at: datetime | None = None
    expires_at: datetime | None = None


class SourceStateOut(ApiModel):
    source_id: str
    name: str
    status: str
    note: str


class EngineStateOut(ApiModel):
    """Движок и то, чем его кормить."""

    key: str
    version: str
    ready: bool = True
    feeds_from: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(default_factory=list)
    # full — все входы движка закрыты подключёнными источниками;
    # partial — часть; none — ни одного.
    coverage: str = "none"


class ResearchStatusOut(ApiModel):
    """Почему выдача выглядит так, как выглядит."""

    engines: list[EngineStateOut] = Field(default_factory=list)
    engines_ready: int = 0
    engines_fed: int = 0
    total_sources: int = 0
    free_route: int = 0
    awaiting_terms_check: list[str] = Field(default_factory=list)
    manual_only: list[str] = Field(default_factory=list)
    unavailable: list[SourceStateOut] = Field(default_factory=list)
    connect_order: list[str] = Field(default_factory=list)
    hypotheses: int = 0
    reason: str = ""


class ResearchResponse(ApiModel):
    status: ResearchStatusOut
    hypotheses: list[HypothesisOut] = Field(default_factory=list)


# Как состояние называется на экране. Английские коды остаются в базе и в
# API, но владельцу показывается человеческое слово.
STATE_LABELS = {
    HypothesisState.OBSERVATION: "наблюдение",
    HypothesisState.EARLY_CANDIDATE: "ранний кандидат",
    HypothesisState.CONFIRMED: "подтверждённая гипотеза",
    HypothesisState.DILIGENCE_READY: "к углублённому анализу",
    HypothesisState.INVALIDATED: "опровергнута",
    HypothesisState.EXPIRED: "срок вышел",
    HypothesisState.REJECTED: "отклонена",
}

# Состояния, которые показываются по умолчанию: живые.
LIVE_STATES = (
    HypothesisState.OBSERVATION,
    HypothesisState.EARLY_CANDIDATE,
    HypothesisState.CONFIRMED,
    HypothesisState.DILIGENCE_READY,
)


def _evidence(session: Session, hypothesis_id) -> list[EvidenceOut]:
    rows = session.execute(
        select(HypothesisEvidence).where(
            HypothesisEvidence.hypothesis_id == hypothesis_id
        )
    ).scalars()
    return [
        EvidenceOut(
            role=str(row.role),
            claim=row.claim_text,
            lineage_root_id=row.lineage_root_id,
            independence_group=row.independence_group,
            confidence=row.confidence,
        )
        for row in rows
    ]


def _engines(session: Session) -> list[EngineStateOut]:
    """Состояние движков: что посчитано и чего не хватает для расчёта.

    Разделение важнее, чем кажется. «Движок не написан» и «движок написан,
    но кормить его нечем» выглядят снаружи одинаково — пустым экраном, — а
    требуют совершенно разного: первое работы, второе проверки условий
    использования источника. Без этого списка владелец не знает, что
    именно он может сделать сегодня.
    """
    feeds: dict[str, list[str]] = {}
    blocked: dict[str, list[str]] = {}
    # Полнота по подключённым источникам: full только если хотя бы один
    # работающий источник закрывает движок целиком.
    fullness: dict[str, str] = {}
    live = {
        row.source_id
        for row in session.execute(select(ResearchSource)).scalars()
        if row.enabled and row.terms_review_due_at is not None
    }
    for spec in ALL_SOURCES:
        for key, depth in spec.feeds.items():
            feeds.setdefault(key, []).append(spec.source_id)
            if spec.source_id not in live:
                blocked.setdefault(key, []).append(spec.source_id)
                continue
            if depth == "full":
                fullness[key] = "full"
            elif fullness.get(key) != "full":
                fullness[key] = "partial"

    result: list[EngineStateOut] = []
    for engine in ENGINES:
        key = engine.STRATEGY_KEY
        sources = sorted(feeds.get(key, ()))
        missing = sorted(blocked.get(key, ()))
        result.append(
            EngineStateOut(
                key=key,
                version=engine.STRATEGY_VERSION,
                # Движок написан и проверен всегда: сюда попадают только
                # реализованные. «Готов» здесь про код, а не про данные.
                ready=True,
                feeds_from=sources,
                blocked_by=missing,
                # Полнота важнее наличия. Движок, у которого подключена
                # половина входов, считает половину картины — и обещать по
                # нему полный ответ значит обещать несуществующее.
                coverage=fullness.get(key, "none"),
            )
        )
    return result


def _out(session: Session, row: ResearchHypothesis) -> HypothesisOut:
    return HypothesisOut(
        id=str(row.id),
        version=row.version,
        market=row.market,
        entity_id=row.entity_id,
        instrument_id=row.instrument_id,
        symbol=row.instrument_id.split(":")[-1] if row.instrument_id else "",
        title=row.title,
        direction=str(row.direction),
        state=str(row.state),
        state_label=STATE_LABELS.get(row.state, str(row.state)),
        as_of=row.as_of,
        expected_lag=row.expected_lag,
        evidence_score=row.evidence_score,
        economic_score=row.economic_score,
        market_context_score=row.market_context_score,
        market_context_state=row.market_context_state,
        research_priority=row.research_priority,
        three_two_one=row.three_two_one_json or {},
        target_kpis=row.target_kpis_json or [],
        causal_path=row.causal_path_json or {},
        fact_summary=row.fact_summary_json or [],
        alternative_explanations=row.alternative_explanations_json or [],
        risk_flags=[str(f) for f in (row.risk_flags or [])],
        falsifiers=row.falsifiers_json or [],
        missing_evidence=row.missing_evidence_json or [],
        evidence=_evidence(session, row.id),
        state_reason=row.state_reason,
        next_review_at=row.next_review_at,
        expires_at=row.expires_at,
    )


@router.get("/hypotheses", response_model=ResearchResponse)
def hypotheses(
    session: Session = Depends(get_db),
    market: str | None = Query(default=None),
    include_closed: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
) -> ResearchResponse:
    """Гипотезы вместе с состоянием источников.

    Состояние едет всегда, даже когда гипотезы есть: список источников,
    ждущих проверки условий, — это то, что владелец может сделать сегодня,
    и прятать его до пустого экрана незачем.
    """
    query = select(ResearchHypothesis)
    if not include_closed:
        query = query.where(
            ResearchHypothesis.state.in_([s.value for s in LIVE_STATES])
        )
    if market:
        query = query.where(ResearchHypothesis.market == market)
    rows = list(
        session.execute(
            query.order_by(ResearchHypothesis.research_priority.desc()).limit(limit)
        ).scalars()
    )

    state = readiness(session)
    reason = ""
    if not rows:
        if state["free_route"] and state["awaiting_terms_check"]:
            reason = (
                f"бесплатные маршруты есть у {state['free_route']} источников, но "
                f"{len(state['awaiting_terms_check'])} из них ждут проверки условий "
                "использования — до неё сбор не запускается"
            )
        elif not state["free_route"]:
            reason = "ни один источник не подключён: собирать нечего"
        else:
            reason = "источники подключены, гипотез пока нет"

    engines = _engines(session)
    return ResearchResponse(
        status=ResearchStatusOut(
            engines=engines,
            engines_ready=sum(1 for e in engines if e.ready),
            engines_fed=sum(1 for e in engines if e.coverage == "full"),
            total_sources=state["total"],
            free_route=state["free_route"],
            awaiting_terms_check=state["awaiting_terms_check"],
            manual_only=state["manual_only"],
            unavailable=[
                SourceStateOut(
                    source_id=item["source_id"],
                    name=item["name"],
                    status=item["status"],
                    note=item["note"],
                )
                for item in state["paid_or_prohibited"]
            ],
            connect_order=state["connect_order"],
            hypotheses=len(rows),
            reason=reason,
        ),
        hypotheses=[_out(session, row) for row in rows],
    )


class HostReachOut(ApiModel):
    """Один хост источника."""

    host: str = ""
    #: Выпустила ли сеть сервера. Это и есть вопрос про брандмауэр.
    network_open: bool = False
    #: Ответил ли источник по HTTP. Любой код — это ответ.
    answered: bool = False
    status: int | None = None
    detail: str = ""


class ReachabilityOut(ApiModel):
    """Дошёл ли запрос до источника.

    `reachable` — про сеть, а не про здоровье источника: молчащий сервер
    при открытом исходящем доступе достижим. Слить их в один флаг значило
    бы посылать владельца править брандмауэр из-за чужого простоя.
    """

    source_id: str
    host: str = ""
    reachable: bool = False
    status: int | None = None
    detail: str = ""
    hosts: list[HostReachOut] = []


@router.get("/sources/reachability", response_model=list[ReachabilityOut])
def reachability(session: Session = Depends(get_db)) -> list[ReachabilityOut]:
    """Живая проверка доступности источников после деплоя.

    Нужна затем, что разрешить сбор и суметь его сделать — разные вещи.
    Правовой шлюз может пропускать, а исходящий доступ с сервера быть
    закрыт, и снаружи это выглядит одинаково: гипотез нет. Проверка
    разделяет «нельзя по праву» и «не пускает сеть».

    Пропуск запрашивается по-настоящему, а не в обход: у заблокированного
    источника проба не уходит в сеть вовсе, и это видно в ответе. Пробовать
    достучаться туда, куда нельзя, — то же нарушение, что и сбор.
    """
    result: list[ReachabilityOut] = []
    for spec in ALL_SOURCES:
        urls = tuple(u for u in (spec.base_url, *spec.extra_urls) if u)
        if not urls:
            continue
        host = urlsplit(urls[0]).hostname or ""
        try:
            authorize(session, spec.source_id, {"fetch"}, now=datetime.now(UTC))
        except CollectionDenied as denied:
            result.append(
                ReachabilityOut(
                    source_id=spec.source_id,
                    host=host,
                    reachable=False,
                    detail=f"проба не отправлялась: {denied.reason}",
                )
            )
            continue
        result.append(_probe(spec.source_id, urls))
    session.commit()
    return result


def _probe(source_id: str, urls: tuple[str, ...]) -> ReachabilityOut:
    """Пройти по всем хостам источника и свести ответ к одной строке.

    Сводка берёт худшее: источник доступен ровно настолько, насколько
    доступен его самый закрытый хост. У ФНС описания наборов и сами файлы
    лежат на разных хостах, и «один из трёх открыт» — это не «открыт».
    """
    probes = [reach.probe(url) for url in urls]
    closed = [p for p in probes if p.blocked_by_network]
    silent = [p for p in probes if p.network_open and not p.answered]

    if closed:
        detail = "; ".join(f"{p.host}: {p.detail}" for p in closed)
    elif silent:
        detail = "; ".join(f"{p.host}: {p.detail}" for p in silent)
    else:
        detail = "; ".join(f"{p.host}: {p.detail}" for p in probes)

    return ReachabilityOut(
        source_id=source_id,
        host=probes[0].host,
        # Про сеть, а не про здоровье источника: молчащий сервер при
        # открытом доступе достижим, и звать владельца к брандмауэру
        # из-за чужого простоя — ложная тревога.
        reachable=not closed,
        status=probes[0].status,
        detail=detail[:600],
        hosts=[
            HostReachOut(
                host=p.host,
                network_open=p.network_open,
                answered=p.answered,
                status=p.status,
                detail=p.detail,
            )
            for p in probes
        ],
    )


class ModelStateOut(ApiModel):
    """Состояние локальной модели критика."""

    configured: bool = False
    reachable: bool = False
    model: str = ""
    model_installed: bool = False
    installed: list[str] = []
    reason: str = ""


@router.get("/model", response_model=ModelStateOut)
def model_state() -> ModelStateOut:
    """Есть ли модель, доступна ли она и та ли она.

    Три разных «нет», которые снаружи выглядят одинаково пустой выдачей:
    не настроена, не отвечает, установлена другая. Последнее коварнее
    прочих — опечатка в теге выглядит как молчание модели.

    Ключей и адресов наружу не отдаёт: только то, что уже задано локально.
    """
    return ModelStateOut(**gateway.health())


class PlanCheckOut(ApiModel):
    """Один адрес из плана сбора и что он ответил."""

    source_id: str
    kind: str = ""
    url: str = ""
    network_open: bool = False
    answered: bool = False
    status: int | None = None
    detail: str = ""


@router.get("/sources/plan-check", response_model=list[PlanCheckOut])
def plan_check(session: Session = Depends(get_db)) -> list[PlanCheckOut]:
    """Проверить адреса, по которым адаптер собирается ходить на самом деле.

    Доступность хоста и доступность данных — разные вещи, и вторую до сих
    пор не проверял никто. Хост может отвечать 200 на корень и 404 на
    каждый файл, который мы намерены забрать; снаружи разницы не видно, а
    сбор не пойдёт.

    Произвольный адрес сюда передать нельзя: проверяются ровно те ссылки,
    которые вернул `fetch_plan` адаптера, и только после выданного
    пропуска. Иначе это был бы способ ходить чужими руками куда угодно.
    """
    result: list[PlanCheckOut] = []
    now = datetime.now(UTC)
    for source_id, adapter in adapters.BY_SOURCE.items():
        try:
            granted = authorize(session, source_id, {"fetch"}, now=now)
        except CollectionDenied as denied:
            result.append(
                PlanCheckOut(
                    source_id=source_id,
                    detail=f"проба не отправлялась: {denied.reason}",
                )
            )
            continue
        for target in adapter.fetch_plan(granted):
            probe = reach.probe(target.url)
            result.append(
                PlanCheckOut(
                    source_id=source_id,
                    kind=target.kind,
                    url=target.url,
                    network_open=probe.network_open,
                    answered=probe.answered,
                    status=probe.status,
                    detail=probe.detail,
                )
            )
    session.commit()
    return result


class CatalogueOut(ApiModel):
    """Что лежит в точке входа источника."""

    source_id: str
    url: str = ""
    status: int | None = None
    content_type: str = ""
    size: int = 0
    matched: list[str] = []
    sample: list[str] = []
    head: str = ""
    error: str = ""


@router.get("/sources/catalogue", response_model=list[CatalogueOut])
def catalogue(session: Session = Depends(get_db)) -> list[CatalogueOut]:
    """Прочитать каталоги источников: что там есть на самом деле.

    Адаптер обязан находить выгрузки, а не собирать их адреса из имени
    набора. Собранный адрес выглядит правдоподобно и отвечает 404 — то
    есть сбор молча не идёт, экран пуст, ошибок нет, объяснить нечем.
    Именно так и было: ЦБ отвечал 501 на все четыре адреса, ФНС 404.

    Адреса берутся из реестра, как и везде здесь. Произвольную ссылку
    передать нельзя — иначе это был бы способ ходить в сеть чужими руками.
    """
    result: list[CatalogueOut] = []
    now = datetime.now(UTC)
    for spec in ALL_SOURCES:
        if not spec.catalogue_urls:
            continue
        try:
            authorize(session, spec.source_id, {"fetch"}, now=now)
        except CollectionDenied as denied:
            result.append(
                CatalogueOut(
                    source_id=spec.source_id,
                    error=f"проба не отправлялась: {denied.reason}",
                )
            )
            continue
        for url in spec.catalogue_urls:
            found = explore.fetch(url)
            result.append(
                CatalogueOut(
                    source_id=spec.source_id,
                    url=found.url,
                    status=found.status,
                    content_type=found.content_type,
                    size=found.size,
                    matched=found.matched,
                    sample=found.sample,
                    head=found.head,
                    error=found.error,
                )
            )
    session.commit()
    return result


class DiagnoseOut(ApiModel):
    """Подробная проба одного адреса."""

    label: str = ""
    url: str = ""
    forced_ipv4: bool = False
    used_range: bool = False
    http_version: str = ""
    resolved_ip: str = ""
    status: int | None = None
    content_type: str = ""
    content_length: str = ""
    connect_ms: int | None = None
    ttfb_ms: int | None = None
    total_ms: int | None = None
    bytes_read: int = 0
    redirects: list[str] = []
    phase: str = ""
    error: str = ""


@router.get("/sources/diagnose", response_model=list[DiagnoseOut])
def diagnose(session: Session = Depends(get_db)) -> list[DiagnoseOut]:
    """Довести молчание ФНС до однозначного вывода.

    Порядок проб не произволен, он повторяет разветвление причин.

    ЦБ проверяется по `publications`, а не по корню: корень отвечает 404,
    и это ничего не значит — сервис данных состоит из именованных методов.

    У ФНС сначала читаются паспорта наборов, из них берутся настоящие
    адреса. Потом проверяется XSD — он маленький, и если молчит именно он,
    дело не в размере выгрузки. Потом ZIP потоково: заголовки, первый
    кусок, закрыть. `Range` идёт отдельной пробой последним: файловые
    серверы обрабатывают частичный запрос по-разному, и молчание из-за
    неподдержанного заголовка неотличимо от молчания вообще.

    Каждая проба повторяется с принудительным IPv4. Если у хоста есть
    AAAA, а маршрут по IPv6 сломан, соединение уходит именно туда и виснет
    — а выглядит это как молчание сервера.
    """
    result: list[DiagnoseOut] = []
    now = datetime.now(UTC)

    def записать(label: str, detail) -> None:
        result.append(DiagnoseOut(label=label, **vars(detail)))

    # ── ЦБ ──────────────────────────────────────────────────────────────
    try:
        authorize(session, "cbr_data", {"fetch"}, now=now)
    except CollectionDenied as denied:
        result.append(DiagnoseOut(label="cbr:publications", error=str(denied.reason)))
    else:
        for ipv4 in (False, True):
            записать(
                "cbr:publications",
                netdiag.probe(f"{cbr.BASE_URL}/publications", ipv4=ipv4),
            )

    # ── ФНС ─────────────────────────────────────────────────────────────
    try:
        authorize(session, "fns_open_data", {"fetch"}, now=now)
    except CollectionDenied as denied:
        result.append(DiagnoseOut(label="fns", error=str(denied.reason)))
        session.commit()
        return result

    # Каталог отвечает 200 и знает настоящие идентификаторы наборов. Год в
    # имени набора меняется, и угаданный адрес паспорта отвечает 404 —
    # снаружи неотличимо от «источник ничего не приносит».
    каталог = _passport_html(fns.CATALOGUE)
    pages: dict[str, str] = {}
    for target in fns.passport_urls(catalogue_html=каталог):
        записать(f"fns:passport:{target.kind}", netdiag.probe(target.url))
        # Паспорт нужен целиком: ссылки лежат в таблице ниже заголовка.
        полный = _passport_html(target.url)
        if полный:
            pages[target.kind] = полный

    for target in fns.discover(passports=pages):
        if target.kind.endswith(":passport"):
            continue
        поток = not target.kind.endswith(":structure")
        for ipv4 in (False, True):
            записать(
                f"fns:{target.kind}",
                netdiag.probe(target.url, ipv4=ipv4, read_body=поток),
            )
        # Диапазон — дополнительная проба, а не основная.
        записать(
            f"fns:{target.kind}:range",
            netdiag.probe(target.url, use_range=True, read_body=False),
        )

    session.commit()
    return result


def _passport_html(url: str) -> str:
    """Забрать паспорт целиком, чтобы дотянуться до таблицы со ссылками."""
    import urllib.request

    request = urllib.request.Request(
        url, headers={"User-Agent": reach.USER_AGENT, "Accept": "text/html,*/*"}
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return response.read(explore.MAX_BYTES).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — недоступный паспорт виден отдельной пробой
        return ""


@router.post("/sources/sync", response_model=ResearchStatusOut)
def sync_sources(session: Session = Depends(get_db)) -> ResearchStatusOut:
    """Записать реестр источников из кода в базу.

    Управляющий вызов, а не сбор данных: ничего наружу не запрашивает.
    Правовой режим — зафиксированное решение, и живёт оно в репозитории.
    """
    sync_registry(session, now=datetime.now(UTC))
    session.commit()
    state = readiness(session)
    engines = _engines(session)
    return ResearchStatusOut(
        engines=engines,
        engines_ready=sum(1 for e in engines if e.ready),
        engines_fed=sum(1 for e in engines if e.coverage == "full"),
        total_sources=state["total"],
        free_route=state["free_route"],
        awaiting_terms_check=state["awaiting_terms_check"],
        manual_only=state["manual_only"],
        unavailable=[
            SourceStateOut(
                source_id=item["source_id"],
                name=item["name"],
                status=item["status"],
                note=item["note"],
            )
            for item in state["paid_or_prohibited"]
        ],
        connect_order=list(CONNECT_ORDER),
    )


__all__ = ["router"]

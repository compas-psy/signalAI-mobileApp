"""API: контракт §23 и честность ответов.

Проверяется не «эндпоинт вернул 200», а то, ради чего он существует:

* деньги и вероятности уходят строками и не теряют точность;
* незакрытая свеча не попадает в расчётные данные по умолчанию;
* то, за чем ещё нет движка, отвечает 503 с причиной, а не пустотой;
* панель риска отличает «лимит свободен» от «данных нет».
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import get_db
from app.main import app
from app.models import Bar, IdeaEvent, IdeaSkip, Instrument, PaperTrade, TradeIdea
from app.models.enums import (
    AssetClass,
    IdeaStatus,
    QualityStatus,
    SkipReason,
    Venue,
)
from tests.conftest import DEVICE_HEADERS, idea_kwargs


@pytest.fixture
def client(session):
    """Клиент, работающий в той же откатываемой транзакции, что и тест."""
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as c:
        yield c
    app.dependency_overrides.clear()


# ─── Служебное ────────────────────────────────────────────────────────────


def test_health_reports_config_hash_and_paper_mode(client):
    """По /health видно, какими числами считает движок и закрыта ли биржа."""
    body = client.get("/health").json()
    assert len(body["config_hash"]) == 64
    assert body["paper_only"] is True
    assert body["execution_mode"] in ("PAPER", "ANALYTICS_ONLY")
    assert body["engine_version"]


def test_openapi_is_valid_and_covers_contract(client):
    spec = client.get("/openapi.json").json()
    assert spec["openapi"].startswith("3.")
    paths = set(spec["paths"])
    for required in (
        "/api/v1/instruments",
        "/api/v1/ideas",
        "/api/v1/ideas/{idea_id}",
        "/api/v1/ideas/{idea_id}/events",
        "/api/v1/ideas/{idea_id}/approve-paper",
        "/api/v1/ideas/{idea_id}/reject",
        "/api/v1/ideas/scan",
        "/api/v1/risk/dashboard",
        "/api/v1/risk/halt",
    ):
        assert required in paths, required


# ─── Инструменты и свечи ──────────────────────────────────────────────────


def test_instrument_specification_is_exact(client, instrument):
    """Шаг цены и стоимость шага — множители в формуле размера (§17.1).

    Они обязаны доехать до клиента без округления, поэтому едут строками.
    """
    body = client.get("/api/v1/instruments").json()
    item = next(i for i in body if i["instrument_id"] == instrument.instrument_id)
    # Строка, а не JSON-число: double по дороге превратил бы шаг в 0.999…
    assert isinstance(item["tick_size"], str)
    assert Decimal(item["tick_size"]) == Decimal("1")
    assert Decimal(item["contract_multiplier"]) == Decimal("1000")
    assert item["lot_size"] == 1


def test_forming_bar_is_excluded_by_default(client, session, instrument):
    """§4.4: незакрытую свечу нельзя использовать как закрытую.

    Расчёт, случайно захвативший формирующийся бар, меняется сам по себе
    между двумя запросами — поэтому по умолчанию его не отдаём.
    """
    base = datetime(2026, 7, 29, 9, tzinfo=UTC)
    for i, closed in enumerate([True, True, False]):
        session.add(
            Bar(
                instrument_id=instrument.instrument_id,
                timeframe="1h",
                open_time=base + timedelta(hours=i),
                open=Decimal("90000"),
                high=Decimal("90500"),
                low=Decimal("89800"),
                close=Decimal("90200"),
                is_closed=closed,
                source="moex",
            )
        )
    session.flush()

    url = f"/api/v1/market/{instrument.instrument_id}/bars?timeframe=1h"
    closed_only = client.get(url).json()
    assert len(closed_only) == 2
    assert all(b["is_closed"] for b in closed_only)

    everything = client.get(url + "&closed_only=false").json()
    assert len(everything) == 3
    assert everything[-1]["is_closed"] is False


def test_unknown_instrument_is_404(client):
    assert client.get("/api/v1/market/MOEX:FUT:NOPE/bars?timeframe=1h").status_code == 404


def test_regime_absence_explains_itself(client, instrument):
    """Нет режима — есть причина. Пустой объект читался бы как «флэт»."""
    r = client.get(f"/api/v1/market/{instrument.instrument_id}/regime")
    assert r.status_code == 404
    assert "не рассчитан" in r.json()["detail"]


# ─── Идеи ─────────────────────────────────────────────────────────────────


def test_idea_detail_carries_probability_definition(client, session, instrument, now):
    """§32: вероятность без строгого определения — приглашение прочитать её неверно."""
    idea = TradeIdea(**idea_kwargs(instrument.instrument_id, now))
    session.add(idea)
    session.flush()

    body = client.get(f"/api/v1/ideas/{idea.id}").json()
    prob = body["probability"]
    assert "TP1" in prob["definition"] and "SL" in prob["definition"]
    assert isinstance(prob["p_tp1_before_sl"], str)
    assert Decimal(prob["p_tp1_before_sl"]) == Decimal("0.58")
    assert Decimal(prob["confidence"]) == Decimal("0.52")
    assert prob["confidence_band"] == "MEDIUM"
    # Статистики нет — вероятность обязана признать, что она ограничена.
    assert prob["capped"] is True
    assert "OOS" in prob["cap_reason"]


def test_idea_detail_separates_confidence_from_probability(
    client, session, instrument, now
):
    """§15.4: уверенность в оценке и сама оценка — разные числа."""
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            now,
            p_tp1_before_sl=Decimal("0.62"),
            confidence=Decimal("0.30"),
        )
    )
    session.add(idea)
    session.flush()
    prob = client.get(f"/api/v1/ideas/{idea.id}").json()["probability"]
    assert prob["p_tp1_before_sl"] != prob["confidence"]
    assert prob["confidence_band"] == "LOW"


def test_idea_without_tradable_size_says_so(client, session, instrument, now):
    """§20.1: объём меньше лота → идея информационная, а не «с нулевым риском»."""
    idea = TradeIdea(
        **idea_kwargs(instrument.instrument_id, now, quantity=Decimal("0"))
    )
    session.add(idea)
    session.flush()
    sizing = client.get(f"/api/v1/ideas/{idea.id}").json()["sizing"]
    assert sizing["tradable"] is False
    assert "минимального лота" in sizing["not_tradable_reason"]


def test_untradable_reason_comes_from_the_calculation(client, session, instrument, now):
    """«Курс неизвестен» и «объём меньше лота» требуют разных действий."""
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            now,
            quantity=Decimal("0"),
            explanation_json={
                "headline": "тест",
                "sizing_note": "курс USDT к рублю неизвестен: размер позиции "
                "по рублёвому бюджету не посчитать (§17.1)",
            },
        )
    )
    session.add(idea)
    session.flush()
    sizing = client.get(f"/api/v1/ideas/{idea.id}").json()["sizing"]
    assert "курс USDT" in sizing["not_tradable_reason"]


def test_sizing_says_what_the_quantity_is_measured_in(client, session, now):
    """«28» без единицы измерения — не размер позиции, а число."""
    coin = Instrument(
        instrument_id="CRYPTO:PERP:ETHUSDT",
        venue=Venue.CRYPTO,
        asset_class=AssetClass.CRYPTO_PERPETUAL,
        symbol="ETHUSDT",
        title="Ethereum perpetual",
        currency="USDT",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        in_universe=True,
    )
    session.add(coin)
    session.flush()
    idea = TradeIdea(
        **idea_kwargs(
            coin.instrument_id,
            now,
            quantity=Decimal("0.348"),
            risk_per_unit=Decimal("20.16"),
            risk_amount=Decimal("561.42"),
            explanation_json={
                "headline": "тест",
                "quote_currency": "USDT",
                "quote_rate_rub": "80.0",
                "quote_note": "USDT по курсу 80.0000 ₽",
            },
        )
    )
    session.add(idea)
    session.flush()
    sizing = client.get(f"/api/v1/ideas/{idea.id}").json()["sizing"]
    # Номинал дробный и остаётся дробным: 0,348 монеты, а не «ноль штук».
    assert Decimal(sizing["quantity"]) == Decimal("0.348")
    assert Decimal(sizing["quantity_step"]) == Decimal("0.001")
    assert sizing["quantity_unit"] == "ETH"
    assert sizing["quote_currency"] == "USDT"
    assert Decimal(sizing["quote_rate_rub"]) == Decimal("80")
    assert "80" in sizing["quote_note"]


def test_events_are_returned_in_order(client, session, instrument, now):
    idea = TradeIdea(**idea_kwargs(instrument.instrument_id, now))
    session.add(idea)
    session.flush()
    for i, status in enumerate(["DISCOVERED", "WATCH", "TRIGGERED"], start=1):
        session.add(
            IdeaEvent(
                idea_id=idea.id,
                sequence=i,
                new_status=status,
                reason_code=f"step-{i}",
                config_hash="0" * 64,
                engine_version="0.1.0",
            )
        )
    session.flush()
    body = client.get(f"/api/v1/ideas/{idea.id}/events").json()
    assert [e["sequence"] for e in body] == [1, 2, 3]
    assert [e["new_status"] for e in body] == ["DISCOVERED", "WATCH", "TRIGGERED"]


def test_unpresented_ideas_are_still_listed(client, session, instrument, now):
    """UX-ТЗ §12: хранить надо любую идею, включая не показанную владельцу."""
    session.add(TradeIdea(**idea_kwargs(instrument.instrument_id, now)))
    session.flush()
    assert len(client.get("/api/v1/ideas").json()) == 1
    assert client.get("/api/v1/ideas?presented_only=true").json() == []


def test_today_says_why_there_is_nothing(client):
    """«Сделок нет» — валидный результат и обязан быть назван (§0.7, §32)."""
    body = client.get("/api/v1/ideas/today").json()
    assert body["trade_now"] == [] and body["wait_for_trigger"] == []
    assert "не создаёт сделки ради нормы" in body["no_trade_reason"]


def test_scan_reports_what_it_did(client, instrument):
    """Скан обязан отчитаться, а не просто вернуть список.

    Пустая выдача без числа просмотренных инструментов и без причин отказа
    неотличима от сломанного движка.
    """
    body = client.post("/api/v1/ideas/scan").json()
    assert body["scanned"] >= 1
    assert "started_at" in body and "finished_at" in body
    # Инструмент без истории обязан попасть в пропуски с причиной.
    assert body["skipped"], "инструмент без данных исчез молча"
    assert any("баров" in s["reason"] for s in body["skipped"])


def test_scan_with_no_setups_says_why(client):
    """«Сделок нет» остаётся результатом, а не пустотой."""
    body = client.post("/api/v1/ideas/scan").json()
    assert body["produced"] == 0
    assert body["trade_now"] == []
    assert "не создаёт сделки ради нормы" in body["no_trade_reason"]


def _actionable_idea(session, instrument, **overrides) -> TradeIdea:
    moment = datetime.now(UTC)
    values = idea_kwargs(
        instrument.instrument_id,
        moment,
        status=IdeaStatus.TRIGGERED,
        quality_status=QualityStatus.ACTIVE,
        was_presented=True,
    )
    values.update(overrides)
    idea = TradeIdea(**values)
    session.add(idea)
    session.flush()
    return idea


@pytest.mark.parametrize(
    ("quality", "status", "bucket"),
    [
        (QualityStatus.ACTIVE, IdeaStatus.WATCH, "wait_for_trigger"),
        (QualityStatus.WATCH, IdeaStatus.TRIGGERED, "wait_for_trigger"),
        (QualityStatus.ACTIVE, IdeaStatus.TRIGGERED, "trade_now"),
    ],
)
def test_today_uses_the_same_actionable_status_pair_as_approval(
    client, session, instrument, quality, status, bucket
):
    idea = _actionable_idea(
        session,
        instrument,
        quality_status=quality,
        status=status,
    )

    body = client.get("/api/v1/ideas/today").json()
    other = "trade_now" if bucket == "wait_for_trigger" else "wait_for_trigger"

    assert [row["id"] for row in body[bucket]] == [str(idea.id)]
    assert body[other] == []
    if bucket == "wait_for_trigger":
        assert "ожидающие триггера" in body["no_trade_reason"]
    else:
        assert body["no_trade_reason"] == ""


def test_today_does_not_show_quality_rejected_as_waiting(
    client, session, instrument
):
    _actionable_idea(
        session,
        instrument,
        quality_status=QualityStatus.REJECTED,
        status=IdeaStatus.TRIGGERED,
    )

    body = client.get("/api/v1/ideas/today").json()

    assert body["trade_now"] == []
    assert body["wait_for_trigger"] == []
    assert "не создаёт сделки ради нормы" in body["no_trade_reason"]


def test_paper_approval_requires_device_bearer_then_reaches_endpoint(
    client, session, instrument
):
    idea = _actionable_idea(session, instrument)

    with TestClient(app) as anonymous:
        assert anonymous.get("/health").status_code == 200
        denied = anonymous.post(f"/api/v1/ideas/{idea.id}/approve-paper")

    assert denied.status_code == 401
    assert denied.headers["www-authenticate"] == "Bearer"
    assert session.scalar(select(func.count()).select_from(PaperTrade)) == 0

    allowed = client.post(f"/api/v1/ideas/{idea.id}/approve-paper")
    assert allowed.status_code == 200
    assert allowed.json()["decision"] == "APPROVED_PAPER"
    assert allowed.json()["paper_only"] is True


def test_paper_approval_creates_normalized_trade_from_idea_plan(
    client, session, instrument
):
    idea = _actionable_idea(session, instrument)

    response = client.post(f"/api/v1/ideas/{idea.id}/approve-paper")

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "APPROVED_PAPER"
    assert body["paper_only"] is True
    assert body["idempotent_replay"] is False
    assert body["idea_status"] == "ACTIVE"
    trade = body["trade"]
    assert trade["idea_id"] == str(idea.id)
    assert trade["instrument_id"] == instrument.instrument_id
    assert trade["status"] == "PENDING"
    assert Decimal(trade["entry"]) == idea.entry_reference
    assert Decimal(trade["initial_stop"]) == idea.stop
    assert [Decimal(value) for value in trade["tp_prices"]] == [
        idea.tp1,
        idea.tp2,
        idea.tp3,
    ]
    assert session.scalar(select(func.count()).select_from(PaperTrade)) == 1
    event = session.execute(
        select(IdeaEvent).where(IdeaEvent.idea_id == idea.id)
    ).scalar_one()
    assert event.reason_code == "user_approved_paper"
    assert event.user_action is True


def test_paper_approval_is_idempotent(client, session, instrument):
    idea = _actionable_idea(session, instrument)

    first = client.post(f"/api/v1/ideas/{idea.id}/approve-paper").json()
    second = client.post(f"/api/v1/ideas/{idea.id}/approve-paper").json()

    assert second["idempotent_replay"] is True
    assert second["trade"]["id"] == first["trade"]["id"]
    assert session.scalar(select(func.count()).select_from(PaperTrade)) == 1
    assert session.scalar(
        select(func.count()).select_from(IdeaEvent).where(
            IdeaEvent.idea_id == idea.id,
            IdeaEvent.reason_code == "user_approved_paper",
        )
    ) == 1


def test_paper_approval_conflicts_with_other_live_trade_on_instrument(
    client, session, instrument
):
    first = _actionable_idea(session, instrument)
    second = _actionable_idea(session, instrument)
    assert client.post(f"/api/v1/ideas/{first.id}/approve-paper").status_code == 200

    response = client.post(f"/api/v1/ideas/{second.id}/approve-paper")

    assert response.status_code == 409
    assert "другая paper-сделка" in response.json()["detail"]
    assert session.scalar(select(func.count()).select_from(PaperTrade)) == 1


@pytest.mark.parametrize(
    ("status", "quality", "detail"),
    [
        (IdeaStatus.WATCH, QualityStatus.WATCH, "quality_status=WATCH"),
        (IdeaStatus.TRIGGERED, QualityStatus.REJECTED, "quality_status=REJECTED"),
        (IdeaStatus.WATCH, QualityStatus.ACTIVE, "не подтверждена рынком"),
    ],
)
def test_paper_approval_rejects_non_actionable_idea(
    client, session, instrument, status, quality, detail
):
    idea = _actionable_idea(
        session,
        instrument,
        status=status,
        quality_status=quality,
    )

    response = client.post(f"/api/v1/ideas/{idea.id}/approve-paper")

    assert response.status_code == 409
    assert detail in response.json()["detail"]
    assert session.scalar(select(func.count()).select_from(PaperTrade)) == 0


def test_paper_approval_rejects_stale_idea(client, session, instrument):
    moment = datetime.now(UTC)
    idea = _actionable_idea(
        session,
        instrument,
        signal_time=moment - timedelta(days=6),
        expires_at=moment - timedelta(minutes=1),
    )

    response = client.post(f"/api/v1/ideas/{idea.id}/approve-paper")

    assert response.status_code == 409
    assert "устарела" in response.json()["detail"]
    assert session.scalar(select(func.count()).select_from(PaperTrade)) == 0


def test_reject_is_append_only_idempotent_and_hides_idea_from_today(
    client, session, instrument
):
    idea = _actionable_idea(session, instrument)
    payload = {"reason": SkipReason.NO_TRUST.value, "comment": "не верю объёму"}

    first = client.post(f"/api/v1/ideas/{idea.id}/reject", json=payload)
    replay = client.post(
        f"/api/v1/ideas/{idea.id}/reject",
        json={"reason": SkipReason.OTHER.value, "comment": "не перезаписывать"},
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert first.json()["decision"] == "REJECTED"
    assert first.json()["idea_status"] == "CANCELLED"
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["reason"] == SkipReason.NO_TRUST.value
    assert replay.json()["comment"] == "не верю объёму"
    assert session.scalar(select(func.count()).select_from(IdeaSkip)) == 1
    event = session.execute(
        select(IdeaEvent).where(IdeaEvent.idea_id == idea.id)
    ).scalar_one()
    assert event.reason_code == "user_rejected"
    assert event.user_action is True
    assert client.get("/api/v1/ideas/today").json()["trade_now"] == []
    assert any(
        row["id"] == str(idea.id) for row in client.get("/api/v1/ideas").json()
    )


def test_approved_and_rejected_decisions_cannot_cross(
    client, session, instrument
):
    approved = _actionable_idea(session, instrument)
    assert client.post(f"/api/v1/ideas/{approved.id}/approve-paper").status_code == 200
    rejected_after = client.post(
        f"/api/v1/ideas/{approved.id}/reject",
        json={"reason": SkipReason.OTHER.value},
    )
    assert rejected_after.status_code == 409

    other = Instrument(
        instrument_id="MOEX:FUT:BRU6",
        venue=Venue.MOEX,
        asset_class=AssetClass.FUTURES,
        symbol="BRU6",
        title="Brent, сентябрь 2026",
        currency="RUB",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("7.5"),
        quantity_step=Decimal("1"),
        min_quantity=Decimal("1"),
        contract_multiplier=Decimal("10"),
        in_universe=True,
    )
    session.add(other)
    session.flush()
    rejected = _actionable_idea(session, other)
    assert client.post(
        f"/api/v1/ideas/{rejected.id}/reject",
        json={"reason": SkipReason.OTHER.value},
    ).status_code == 200
    approved_after = client.post(f"/api/v1/ideas/{rejected.id}/approve-paper")
    assert approved_after.status_code == 409
    assert "отклонена" in approved_after.json()["detail"]


# ─── Риск ─────────────────────────────────────────────────────────────────


def test_risk_dashboard_distinguishes_empty_from_free(client):
    """Ноль расхода без данных — не «лимит свободен», и панель это говорит."""
    body = client.get("/api/v1/risk/dashboard").json()
    assert body["has_data"] is False
    assert "не свободный лимит" in body["note"]
    assert body["paper_only"] is True


def test_risk_limits_match_engine_tz(client):
    body = client.get("/api/v1/risk/dashboard").json()
    limits = {row["name"]: row["limit"] for row in body["limits"]}
    assert Decimal(limits["daily"]) == Decimal("0.015")
    assert Decimal(limits["weekly"]) == Decimal("0.035")
    assert Decimal(limits["monthly"]) == Decimal("0.06")
    assert Decimal(limits["open"]) == Decimal("0.02")
    assert Decimal(limits["cluster"]) == Decimal("0.01")


def test_halt_and_resume_require_owner_step_up_and_keep_audit_monotonic(client, session):
    """Bearer may strengthen safety, but cannot clear it without fresh owner step-up."""
    from sqlalchemy import text

    on = client.post("/api/v1/risk/halt", json={"reason": "проверка"}).json()
    assert on["kill_switch"] is True and on["kill_switch_reason"] == "проверка"

    resume = client.post("/api/v1/risk/resume", json={"reason": "отбой"})
    assert resume.status_code == 409
    assert resume.json()["detail"] == "EXECUTION_KILL_SWITCH_CLEAR_STEP_UP_REQUIRED"

    still_halted = client.get("/api/v1/risk/dashboard").json()
    assert still_halted["kill_switch"] is True
    assert still_halted["kill_switch_reason"] == "проверка"

    actions = [
        r[0]
        for r in session.execute(
            text(
                "SELECT action FROM audit_events "
                "WHERE action IN ('kill_switch_on', 'kill_switch_off') "
                "ORDER BY occurred_at"
            )
        )
    ]
    assert actions == ["kill_switch_on"]


# ─── Состояние загрузки ───────────────────────────────────────────────────


def test_status_distinguishes_no_data_from_no_setups(client, session, instrument):
    """«Идей нет» и «данных нет» — разные новости, и различить их обязан сервер."""
    body = client.get("/api/v1/market/status").json()
    row = next(
        r for r in body["instruments"]
        if r["instrument_id"] == instrument.instrument_id
    )
    assert body["with_data"] == 0
    assert row["daily_bars"] == 0
    assert row["last_bar_time"] is None
    assert row["stale_hours"] is None


def test_status_reports_freshness_and_counts(client, session, instrument):
    now = datetime.now(UTC)
    for i in range(3):
        session.add(
            Bar(
                instrument_id=instrument.instrument_id, timeframe="1d",
                open_time=now - timedelta(days=i + 1),
                open=Decimal(100), high=Decimal(101), low=Decimal(99),
                close=Decimal(100), is_closed=True, source="test",
            )
        )
    session.flush()

    body = client.get("/api/v1/market/status").json()
    row = next(
        r for r in body["instruments"]
        if r["instrument_id"] == instrument.instrument_id
    )
    assert row["daily_bars"] == 3
    assert row["hourly_bars"] == 0
    assert 23 <= row["stale_hours"] <= 25
    assert body["with_data"] == 1


def test_idea_sized_before_the_currency_fix_stops_being_tradable(
    client, session, now
):
    """Объём «28 ETH» на депозит в миллион рублей — не размер, а ошибка.

    Идея неизменяема: пересчитать её задним числом нельзя, по ней могли
    принять решение. Но и предлагать к исполнению объём, полученный делением
    рублей на доллары, нельзя тем более.
    """
    coin = Instrument(
        instrument_id="CRYPTO:PERP:ETHUSDT",
        venue=Venue.CRYPTO,
        asset_class=AssetClass.CRYPTO_PERPETUAL,
        symbol="ETHUSDT",
        currency="USDT",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        in_universe=True,
    )
    session.add(coin)
    session.flush()
    idea = TradeIdea(
        **idea_kwargs(
            coin.instrument_id,
            now,
            quantity=Decimal("28"),
            # Объяснение старого формата: про валюту котировки в нём ничего.
            explanation_json={"headline": "старая идея"},
        )
    )
    session.add(idea)
    session.flush()
    sizing = client.get(f"/api/v1/ideas/{idea.id}").json()["sizing"]
    assert sizing["tradable"] is False
    assert "до пересчёта валют" in sizing["not_tradable_reason"]


def test_ruble_idea_of_the_old_format_is_untouched(client, session, instrument, now):
    """У фьючерса MOEX пересчитывать было нечего — идея остаётся исполнимой."""
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            now,
            quantity=Decimal("4"),
            explanation_json={"headline": "старая идея"},
        )
    )
    session.add(idea)
    session.flush()
    sizing = client.get(f"/api/v1/ideas/{idea.id}").json()["sizing"]
    assert sizing["tradable"] is True
    assert sizing["quote_currency"] == "RUB"


def test_closed_idea_says_why_not_just_that(client, session, instrument, now):
    """«Рынок обогнал» — это отметка. Владельцу нужно, что именно случилось."""
    from app.journal.lifecycle import TransitionRequest, transition

    idea = TradeIdea(**idea_kwargs(instrument.instrument_id, now))
    session.add(idea)
    session.flush()
    transition(
        session,
        idea,
        TransitionRequest(
            new_status=IdeaStatus.MISSED,
            reason_code="price_left_without_entry",
            reason_detail="цена дошла до дальней цели 88000, ни разу не зайдя "
            "в зону входа 90000-90200: сетап отработал без нас",
        ),
    )
    session.flush()

    body = client.get(f"/api/v1/ideas/{idea.id}").json()
    assert body["status"] == "MISSED"
    assert "не зайдя в зону входа" in body["closing_reason"]


def test_live_idea_has_no_closing_reason(client, session, instrument, now):
    idea = TradeIdea(**idea_kwargs(instrument.instrument_id, now))
    session.add(idea)
    session.flush()
    assert client.get(f"/api/v1/ideas/{idea.id}").json()["closing_reason"] == ""

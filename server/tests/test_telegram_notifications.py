from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import TradeIdea
from app.notification_outbox import emit
from app import telegram_notifications as telegram
from tests.conftest import idea_kwargs


def test_disabled_period_is_not_replayed_when_telegram_is_reenabled(
    session: Session,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHATID", "123")
    sent: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(
        telegram,
        "_telegram_request",
        lambda method, fields, **_: sent.append((method, fields)) or {"ok": True},
    )

    assert telegram.status(session) == {"enabled": True, "configured": True}
    telegram.set_enabled(session, False)
    emit(
        session,
        key="test:telegram:disabled",
        kind="PAPER",
        title="Не отправлять",
        body="Событие создано, пока Telegram выключен",
    )
    assert telegram.deliver(session) == 0

    telegram.set_enabled(session, True)
    assert telegram.deliver(session) == 0
    emit(
        session,
        key="test:telegram:enabled",
        kind="PAPER",
        title="Отправить",
        body="Новое событие после включения",
    )
    assert telegram.deliver(session) == 1
    assert telegram.deliver(session) == 0
    assert [method for method, _ in sent] == ["sendMessage"]
    assert "Новое событие после включения" in sent[0][1]["text"]


def test_idea_notification_contains_trade_plan_chart_and_deeplink(
    session: Session,
    instrument,
    now,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHATID", "123")
    telegram.status(session)

    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            now,
            status="TRIGGERED",
            quality_status="ACTIVE",
            was_presented=True,
            risk_amount=Decimal("500"),
            quantity=Decimal("2"),
            explanation_json={
                "risk_policy": {"tp_shares": ["0.4", "0.4", "0.2"]}
            },
            annotations_json=[
                {
                    "type": "smcBos",
                    "evidence_id": "smc",
                    "start_time": now.isoformat(),
                    "end_time": now.isoformat(),
                    "price_low": "90000",
                    "price_high": "90000",
                    "label": "BOS вверх",
                }
            ],
        )
    )
    session.add(idea)
    session.flush()
    emit(
        session,
        key=f"idea:{idea.id}:telegram-test",
        kind="IDEA",
        title="SIU6 · можно действовать",
        body="test",
        payload=f"idea:{idea.id}",
    )

    png = b"\x89PNG\r\n\x1a\ntelegram-chart"
    rendered: dict[str, str] = {}

    def fake_render(*_, **kwargs):
        rendered["deeplink"] = kwargs["deeplink"]
        return png

    monkeypatch.setattr(telegram, "render_idea_chart", fake_render)
    sent: list[tuple[str, dict[str, str], bytes | None]] = []

    def fake_request(method, fields, *, photo=None):
        sent.append((method, fields, photo))
        return {"ok": True}

    monkeypatch.setattr(telegram, "_telegram_request", fake_request)

    assert telegram.deliver(session) == 1
    assert len(sent) == 1
    method, fields, photo = sent[0]
    assert method == "sendPhoto"
    assert photo == png
    caption = fields["caption"]
    for required in (
        "Вход:",
        "SL:",
        "TP1:",
        "TP2:",
        "TP3:",
        "Потенциальный доход:",
        "Потенциальный убыток:",
        "R:R:",
    ):
        assert required in caption
    assert "+" in caption
    assert "−500 ₽" in caption
    assert f"/open/idea/{idea.id}" in fields["reply_markup"]
    assert rendered["deeplink"].endswith(f"/open/idea/{idea.id}")
    assert telegram.deliver(session) == 0


def test_public_idea_link_bridges_to_android_deeplink() -> None:
    idea_id = "8b9f7e3f-a45a-4bd4-a194-2014c5f0b1c6"
    response = TestClient(app).get(f"/open/idea/{idea_id}")
    assert response.status_code == 200
    assert f"signalai://idea/{idea_id}" in response.text

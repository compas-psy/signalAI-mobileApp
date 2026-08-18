"""Telegram delivery for the same durable outbox used by Android push."""

from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal
from urllib import parse, request

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .models import Instrument, NotificationOutbox, TradeIdea
from .notification_outbox import NotificationEvent, list_after
from .paper.management_policy import targets_with_policy
from .telegram_chart import render_idea_chart

_PUBLIC_BASE = "https://api.ilyamartynov.ru"
_CHANNEL = "telegram"


def configured() -> bool:
    return bool(os.environ.get("TELEGRAM_TOKEN", "").strip()) and bool(
        os.environ.get("TELEGRAM_CHATID", "").strip()
    )


def _tail(session: Session) -> int:
    return int(
        session.execute(select(func.coalesce(func.max(NotificationOutbox.id), 0))).scalar_one()
    )


def _ensure_state(session: Session) -> None:
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS signalai_notification_channels (
                channel TEXT PRIMARY KEY,
                enabled BOOLEAN NOT NULL,
                cursor BIGINT NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    session.execute(
        text(
            """
            INSERT INTO signalai_notification_channels(channel, enabled, cursor)
            VALUES (:channel, true, :cursor)
            ON CONFLICT (channel) DO NOTHING
            """
        ),
        {"channel": _CHANNEL, "cursor": _tail(session)},
    )


def status(session: Session) -> dict[str, bool]:
    _ensure_state(session)
    enabled = bool(
        session.execute(
            text(
                "SELECT enabled FROM signalai_notification_channels "
                "WHERE channel = :channel"
            ),
            {"channel": _CHANNEL},
        ).scalar_one()
    )
    return {"enabled": enabled, "configured": configured()}


def set_enabled(session: Session, enabled: bool) -> dict[str, bool]:
    _ensure_state(session)
    current = bool(
        session.execute(
            text(
                "SELECT enabled FROM signalai_notification_channels "
                "WHERE channel = :channel"
            ),
            {"channel": _CHANNEL},
        ).scalar_one()
    )
    cursor = _tail(session) if enabled and not current else None
    session.execute(
        text(
            """
            UPDATE signalai_notification_channels
            SET enabled = :enabled,
                cursor = COALESCE(:cursor, cursor),
                updated_at = now()
            WHERE channel = :channel
            """
        ),
        {"channel": _CHANNEL, "enabled": enabled, "cursor": cursor},
    )
    return {"enabled": enabled, "configured": configured()}


def _advance(session: Session, event_id: int) -> None:
    session.execute(
        text(
            """
            UPDATE signalai_notification_channels
            SET cursor = :cursor, updated_at = now()
            WHERE channel = :channel
            """
        ),
        {"channel": _CHANNEL, "cursor": event_id},
    )


def _telegram_request(
    method: str,
    fields: dict[str, str],
    *,
    photo: bytes | None = None,
) -> dict:
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN is not configured")
    url = f"https://api.telegram.org/bot{token}/{method}"

    if photo is None:
        body = parse.urlencode(fields).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
    else:
        boundary = f"signalai-{uuid.uuid4().hex}"
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                    str(value).encode("utf-8"),
                    b"\r\n",
                ]
            )
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="photo"; filename="signalai.png"\r\n',
                b"Content-Type: image/png\r\n\r\n",
                photo,
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
        )
        body = b"".join(chunks)
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}

    with request.urlopen(request.Request(url, data=body, headers=headers), timeout=12) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("ok") is not True:
        raise RuntimeError(f"Telegram Bot API rejected {method}")
    return payload


def _fmt_price(value: Decimal | None) -> str:
    if value is None:
        return "—"
    raw = f"{value:.8f}".rstrip("0").rstrip(".")
    whole, dot, fraction = raw.partition(".")
    sign = ""
    if whole.startswith("-"):
        sign, whole = "-", whole[1:]
    whole = f"{int(whole):,}".replace(",", " ")
    return f"{sign}{whole}{',' + fraction if dot else ''}"


def _fmt_money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('1')):,.0f}".replace(",", " ") + " ₽"


def _idea_id(event: NotificationEvent) -> str:
    prefix = "idea:"
    if not event.payload.startswith(prefix):
        raise RuntimeError(f"idea notification {event.id} has no idea payload")
    return event.payload[len(prefix) :]


def _deeplink(idea_id: str) -> str:
    return f"{_PUBLIC_BASE}/open/idea/{idea_id}"


def _keyboard(idea_id: str) -> str:
    return json.dumps(
        {
            "inline_keyboard": [
                [{"text": "Открыть идею в SignalAI", "url": _deeplink(idea_id)}]
            ]
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _profit_and_rr(idea: TradeIdea) -> tuple[Decimal, Decimal, Decimal]:
    entry = Decimal(idea.entry_reference)
    stop = Decimal(idea.stop)
    risk_distance = abs(entry - stop)
    loss = Decimal(idea.risk_amount)
    if risk_distance <= 0 or loss <= 0:
        return Decimal(0), loss, Decimal(0)

    targets, shares = targets_with_policy(idea)
    weighted_r = sum(
        (abs(Decimal(target) - entry) / risk_distance) * Decimal(share)
        for target, share in zip(targets, shares, strict=True)
    )
    return loss * weighted_r, loss, weighted_r


def _send_idea(session: Session, event: NotificationEvent) -> None:
    idea_id = _idea_id(event)
    idea = session.get(TradeIdea, uuid.UUID(idea_id))
    if idea is None:
        raise RuntimeError(f"idea {idea_id} no longer exists")
    instrument = session.execute(
        select(Instrument).where(Instrument.instrument_id == idea.instrument_id)
    ).scalar_one()
    profit, loss, rr = _profit_and_rr(idea)
    direction = "LONG" if str(idea.direction) == "LONG" else "SHORT"
    zone = ""
    if idea.entry_low != idea.entry_high:
        zone = f" · зона {_fmt_price(idea.entry_low)}–{_fmt_price(idea.entry_high)}"
    caption = "\n".join(
        [
            f"🚨 {instrument.symbol} · {direction} · {int(idea.score)}/100",
            f"Вход: {_fmt_price(idea.entry_reference)}{zone}",
            f"SL: {_fmt_price(idea.stop)}",
            f"TP1: {_fmt_price(idea.tp1)}",
            f"TP2: {_fmt_price(idea.tp2)}",
            f"TP3: {_fmt_price(idea.tp3)}",
            f"Потенциальный доход: +{_fmt_money(profit)}",
            f"Потенциальный убыток: −{_fmt_money(loss)}",
            f"R:R: {rr:.2f}",
        ]
    )
    link = _deeplink(idea_id)
    _telegram_request(
        "sendPhoto",
        {
            "chat_id": os.environ["TELEGRAM_CHATID"].strip(),
            "caption": caption,
            "reply_markup": _keyboard(idea_id),
        },
        photo=render_idea_chart(session, idea, deeplink=link),
    )


def _send_text(event: NotificationEvent) -> None:
    fields = {
        "chat_id": os.environ["TELEGRAM_CHATID"].strip(),
        "text": f"{event.title}\n{event.body}",
    }
    if event.payload.startswith("idea:"):
        fields["reply_markup"] = _keyboard(event.payload[5:])
    _telegram_request("sendMessage", fields)


def deliver(session: Session, *, limit: int = 20) -> int:
    """Deliver new IDEA/PAPER/RESOURCE outbox events once, in outbox order."""
    state = status(session)
    if not state["enabled"] or not state["configured"]:
        return 0
    cursor = int(
        session.execute(
            text(
                "SELECT cursor FROM signalai_notification_channels "
                "WHERE channel = :channel"
            ),
            {"channel": _CHANNEL},
        ).scalar_one()
    )
    delivered = 0
    for event in list_after(session, cursor, limit=limit):
        if event.kind == "IDEA":
            _send_idea(session, event)
            delivered += 1
        elif event.kind in {"PAPER", "RESOURCE"}:
            _send_text(event)
            delivered += 1
        _advance(session, event.id)
    return delivered


def send_test_message() -> None:
    if not configured():
        raise RuntimeError("Telegram secrets are not configured")
    _telegram_request(
        "sendMessage",
        {
            "chat_id": os.environ["TELEGRAM_CHATID"].strip(),
            "text": "SignalAI · Telegram подключён. Новые идеи и события paper будут приходить сюда параллельно с push.",
        },
    )


__all__ = ["configured", "deliver", "send_test_message", "set_enabled", "status"]

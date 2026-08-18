"""Premium H1 trade-card snapshots for Telegram idea notifications."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Bar, TradeIdea
from .models.enums import Timeframe

_WIDTH = 1200
_HEIGHT = 960
_CARD_TOP = 700
_PLOT = (64, 152, 1048, 646)

_BG_TOP = (9, 15, 28)
_BG_BOTTOM = (17, 28, 48)
_TEXT = "#F8FAFC"
_MUTED = "#94A3B8"
_GRID = "#263449"
_GREEN = "#22C55E"
_RED = "#F43F5E"
_AMBER = "#F5B942"
_INDIGO = "#818CF8"
_DARK = "#0F172A"


def _price(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}".replace(",", " ")
    if abs(value) >= 1:
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _decimal_text(value: Decimal | float | int) -> str:
    raw = f"{Decimal(str(value)):.8f}".rstrip("0").rstrip(".")
    whole, dot, fraction = raw.partition(".")
    sign = ""
    if whole.startswith("-"):
        sign, whole = "-", whole[1:]
    whole = f"{int(whole):,}".replace(",", " ")
    return f"{sign}{whole}{'.' + fraction if dot else ''}"


def _direction_label(idea: TradeIdea) -> str:
    value = getattr(idea.direction, "value", idea.direction)
    return "LONG" if str(value).upper() == "LONG" else "SHORT"


def _h1_countdown(moment: datetime) -> str:
    """Return a static countdown to the next H1 boundary for the PNG snapshot."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    next_hour = moment.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    seconds = max(0, int((next_hour - moment).total_seconds()))
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"


def _trade_card_metrics(idea: TradeIdea) -> dict[str, str]:
    score = Decimal(idea.score)
    probability = Decimal(idea.p_tp1_before_sl) * Decimal(100)
    risk = _decimal_text(Decimal(idea.risk_amount))
    return {
        "score": f"{_decimal_text(score)}/100",
        "probability": f"{probability.quantize(Decimal('1'))}%",
        "position": _decimal_text(Decimal(idea.quantity)),
        "risk": f"{risk} RUB",
    }


def _smc_label(annotation: dict[str, Any]) -> str:
    kind = str(annotation.get("type", ""))
    return {
        "smcBos": "BOS",
        "smcChoch": "CHoCH",
        "smcSweep": "SWEEP",
        "smcFvg": "FVG",
        "smcOrderBlock": "OB",
    }.get(kind, "SMC")


def _gradient(image) -> None:
    from PIL import ImageDraw

    draw = ImageDraw.Draw(image)
    for yy in range(_CARD_TOP):
        ratio = yy / max(_CARD_TOP - 1, 1)
        color = tuple(
            round(a + (b - a) * ratio)
            for a, b in zip(_BG_TOP, _BG_BOTTOM, strict=True)
        )
        draw.line((0, yy, _WIDTH, yy), fill=color)


def _rounded_label(draw, box, text: str, *, fill: str, text_fill: str, font) -> None:
    draw.rounded_rectangle(box, radius=18, fill=fill)
    bounds = draw.textbbox((0, 0), text, font=font)
    text_w = bounds[2] - bounds[0]
    text_h = bounds[3] - bounds[1]
    x1, y1, x2, y2 = box
    draw.text(
        ((x1 + x2 - text_w) / 2, (y1 + y2 - text_h) / 2 - 1),
        text,
        fill=text_fill,
        font=font,
    )


def _qr_image(data: str):
    """Build a scannable in-memory QR code; no external QR service is used."""
    try:
        import qrcode
    except ImportError as exc:  # pragma: no cover - production image provides it
        raise RuntimeError("qrcode runtime dependency is unavailable") from exc

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=4,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color=_DARK, back_color="white").convert("RGB")


def render_idea_chart(
    session: Session,
    idea: TradeIdea,
    *,
    deeplink: str | None = None,
    rendered_at: datetime | None = None,
    limit: int = 96,
) -> bytes:
    """Render audited H1 bars plus immutable plan, SMC marks and owner CTA."""
    from PIL import Image, ImageDraw, ImageFont

    rows = list(
        reversed(
            list(
                session.execute(
                    select(Bar)
                    .where(
                        Bar.instrument_id == idea.instrument_id,
                        Bar.timeframe == Timeframe.H1,
                        Bar.is_closed.is_(True),
                    )
                    .order_by(Bar.open_time.desc())
                    .limit(limit)
                ).scalars()
            )
        )
    )
    if not rows:
        raise RuntimeError(f"H1 bars are unavailable for {idea.instrument_id}")

    annotations = [
        item
        for item in (idea.annotations_json or [])
        if isinstance(item, dict) and item.get("evidence_id") == "smc"
    ]
    values = [float(v) for row in rows for v in (row.low, row.high)]
    values.extend(
        float(value)
        for value in (
            idea.entry_low,
            idea.entry_high,
            idea.stop,
            idea.tp1,
            idea.tp2,
            idea.tp3,
        )
        if value is not None
    )
    for item in annotations:
        for key in ("price_low", "price_high"):
            if item.get(key) is not None:
                values.append(float(item[key]))

    lo, hi = min(values), max(values)
    span = max(hi - lo, max(abs(hi), 1.0) * 0.002)
    lo -= span * 0.07
    hi += span * 0.07

    left, top, right, bottom = _PLOT
    image = Image.new("RGB", (_WIDTH, _HEIGHT), _BG_TOP)
    _gradient(image)
    draw = ImageDraw.Draw(image, "RGBA")

    logo_font = ImageFont.load_default(size=22)
    hero_font = ImageFont.load_default(size=38)
    title_font = ImageFont.load_default(size=27)
    label_font = ImageFont.load_default(size=18)
    body_font = ImageFont.load_default(size=20)
    small_font = ImageFont.load_default(size=15)
    tiny_font = ImageFont.load_default(size=13)

    symbol = idea.instrument_id.split(":")[-1]
    direction = _direction_label(idea)
    metrics = _trade_card_metrics(idea)
    now = rendered_at or datetime.now(UTC)
    direction_color = _GREEN if direction == "LONG" else _RED

    draw.text((64, 30), "SIGNAL AI", fill=_TEXT, font=logo_font)
    draw.text((64, 59), symbol, fill=_TEXT, font=hero_font)
    symbol_bounds = draw.textbbox((64, 59), symbol, font=hero_font)
    pill_left = symbol_bounds[2] + 18
    _rounded_label(
        draw,
        (pill_left, 64, pill_left + 106, 96),
        direction,
        fill=direction_color,
        text_fill="#FFFFFF",
        font=small_font,
    )
    draw.text(
        (64, 113),
        "H1 | SMART MONEY + TRADE PLAN",
        fill=_MUTED,
        font=small_font,
    )
    draw.text((835, 34), "SCORE", fill=_MUTED, font=tiny_font)
    draw.text((835, 54), metrics["score"], fill=_TEXT, font=title_font)
    draw.text((1004, 34), "H1 CLOSE", fill=_MUTED, font=tiny_font)
    draw.text((1004, 54), _h1_countdown(now), fill=_AMBER, font=title_font)

    def y(price: float) -> float:
        return bottom - (price - lo) / (hi - lo) * (bottom - top)

    count = len(rows)
    step = (right - left) / max(count, 1)

    def x(index: int) -> float:
        return left + (index + 0.5) * step

    for i in range(6):
        yy = top + (bottom - top) * i / 5
        price = hi - (hi - lo) * i / 5
        draw.line((left, yy, right, yy), fill=_GRID, width=1)
        draw.text(
            (right + 14, yy - 8),
            _price(price),
            fill=_MUTED,
            font=small_font,
        )

    for i in range(7):
        xx = left + (right - left) * i / 6
        draw.line((xx, top, xx, bottom), fill="#1E2A3C", width=1)

    candle_width = max(2, min(9, int(step * 0.58)))
    for index, row in enumerate(rows):
        xx = x(index)
        open_price = float(row.open)
        close_price = float(row.close)
        high_price = float(row.high)
        low_price = float(row.low)
        rising = close_price >= open_price
        color = _GREEN if rising else _RED
        draw.line((xx, y(high_price), xx, y(low_price)), fill=color, width=2)
        y1, y2 = y(open_price), y(close_price)
        if abs(y2 - y1) < 2:
            y2 = y1 + (2 if rising else -2)
        draw.rounded_rectangle(
            (
                xx - candle_width / 2,
                min(y1, y2),
                xx + candle_width / 2,
                max(y1, y2),
            ),
            radius=1,
            fill=color,
        )

    def horizontal(price: float, label: str, color: str, *, width: int = 2) -> None:
        yy = y(price)
        draw.line((left, yy, right, yy), fill=color, width=width)
        text = f"{label}  {_price(price)}"
        box = draw.textbbox((0, 0), text, font=label_font)
        w = box[2] - box[0] + 20
        h = box[3] - box[1] + 10
        draw.rounded_rectangle(
            (left + 6, yy - h / 2, left + 6 + w, yy + h / 2),
            radius=7,
            fill="#08101CE8",
            outline=color,
            width=1,
        )
        draw.text(
            (left + 16, yy - h / 2 + 4),
            text,
            fill=color,
            font=label_font,
        )

    entry_low, entry_high = float(idea.entry_low), float(idea.entry_high)
    draw.rectangle(
        (left, y(max(entry_low, entry_high)), right, y(min(entry_low, entry_high))),
        fill="#F5B9421C",
        outline="#F5B942A8",
        width=2,
    )
    horizontal(float(idea.entry_reference), "ENTRY", _AMBER, width=2)
    horizontal(float(idea.stop), "SL", _RED, width=3)
    horizontal(float(idea.tp1), "TP1", _GREEN)
    horizontal(float(idea.tp2), "TP2", _GREEN)
    if idea.tp3 is not None:
        horizontal(float(idea.tp3), "TP3", _GREEN)

    last_price = float(rows[-1].close)
    current_y = y(last_price)
    for xx in range(left, right, 16):
        draw.line(
            (xx, current_y, min(xx + 7, right), current_y),
            fill="#D5DEEA90",
            width=1,
        )
    draw.text(
        (right + 14, current_y - 8),
        f"NOW {_price(last_price)}",
        fill="#D5DEEA",
        font=small_font,
    )

    first_time = rows[0].open_time
    last_time = rows[-1].open_time
    total_seconds = max((last_time - first_time).total_seconds(), 1.0)

    def mark_x(raw: Any) -> float:
        try:
            moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            ratio = (moment - first_time).total_seconds() / total_seconds
            ratio = max(0.0, min(1.0, ratio))
            return left + ratio * (right - left)
        except (TypeError, ValueError):
            return left

    for item in annotations:
        low = item.get("price_low")
        high = item.get("price_high")
        if low is None or high is None:
            continue
        low_f, high_f = float(low), float(high)
        x1 = mark_x(item.get("start_time"))
        x2 = max(x1 + 12, mark_x(item.get("end_time")))
        label = _smc_label(item)
        if abs(high_f - low_f) > (hi - lo) * 0.0001:
            draw.rounded_rectangle(
                (x1, y(max(low_f, high_f)), x2, y(min(low_f, high_f))),
                radius=3,
                fill="#6366F122",
                outline="#818CF8B0",
                width=2,
            )
        else:
            draw.line(
                (x1, y(low_f), min(right, x1 + 90), y(low_f)),
                fill=_INDIGO,
                width=2,
            )
        draw.text(
            (min(right - 70, x1 + 5), y(high_f) - 21),
            label,
            fill="#C7D2FE",
            font=small_font,
        )

    draw.rounded_rectangle(_PLOT, radius=10, outline="#334155", width=1)
    draw.text(
        (left, 664),
        f"{rows[0].open_time:%d.%m %H:%M} -> {rows[-1].open_time:%d.%m %H:%M} UTC",
        fill="#738198",
        font=tiny_font,
    )

    draw.rectangle((0, _CARD_TOP, _WIDTH, _HEIGHT), fill="#F8FAFC")
    draw.line((0, _CARD_TOP, _WIDTH, _CARD_TOP), fill="#DCE4EE", width=1)

    draw.text((64, 732), "TRADE CARD", fill="#64748B", font=small_font)
    draw.text((64, 757), f"{symbol} | {direction}", fill=_DARK, font=title_font)

    metric_columns = [
        (64, "PROB TP1>SL", metrics["probability"]),
        (254, "POSITION", metrics["position"]),
        (410, "RISK", metrics["risk"]),
        (576, "SCORE", metrics["score"]),
    ]
    for xx, label, value in metric_columns:
        draw.text((xx, 806), label, fill="#64748B", font=tiny_font)
        draw.text((xx, 827), value, fill=_DARK, font=body_font)

    plan_items = [
        (64, "ENTRY", idea.entry_reference, _AMBER),
        (218, "SL", idea.stop, _RED),
        (342, "TP1", idea.tp1, _GREEN),
        (474, "TP2", idea.tp2, _GREEN),
        (606, "TP3", idea.tp3, _GREEN),
    ]
    for xx, label, value, color in plan_items:
        draw.text((xx, 869), label, fill="#64748B", font=tiny_font)
        display = "-" if value is None else _price(float(value))
        draw.text((xx, 888), display, fill=color, font=label_font)

    cta_box = (64, 919, 820, 948)
    draw.rounded_rectangle(cta_box, radius=14, fill=_DARK)
    draw.text(
        (89, 925),
        "OPEN IDEA IN SIGNALAI  >",
        fill="#FFFFFF",
        font=small_font,
    )

    if deeplink:
        qr = _qr_image(deeplink)
        qr_x = 930
        qr_y = 744
        image.paste(qr, (qr_x, qr_y))
        qr_w, qr_h = qr.size
        draw.rounded_rectangle(
            (qr_x - 8, qr_y - 8, qr_x + qr_w + 8, qr_y + qr_h + 8),
            radius=12,
            outline="#CBD5E1",
            width=1,
        )
        draw.text(
            (qr_x, min(qr_y + qr_h + 12, 924)),
            "SCAN TO OPEN",
            fill="#64748B",
            font=tiny_font,
        )

    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


__all__ = ["render_idea_chart"]

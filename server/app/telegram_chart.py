"""H1 snapshot for Telegram idea notifications."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Bar, TradeIdea
from .models.enums import Timeframe

_WIDTH = 1200
_HEIGHT = 675
_PLOT = (72, 54, 1110, 610)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def _price(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}".replace(",", " ")
    if abs(value) >= 1:
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _smc_label(annotation: dict[str, Any]) -> str:
    kind = str(annotation.get("type", ""))
    return {
        "smcBos": "BOS",
        "smcChoch": "CHoCH",
        "smcSweep": "SWEEP",
        "smcFvg": "FVG",
        "smcOrderBlock": "OB",
    }.get(kind, "SMC")


def render_idea_chart(session: Session, idea: TradeIdea, *, limit: int = 96) -> bytes:
    """Render audited H1 bars plus the idea's immutable SMC/trade-plan marks."""
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
    lo -= span * 0.06
    hi += span * 0.06

    left, top, right, bottom = _PLOT
    image = Image.new("RGB", (_WIDTH, _HEIGHT), "#111318")
    draw = ImageDraw.Draw(image, "RGBA")
    title_font = _font(24)
    label_font = _font(18)
    small_font = _font(15)

    draw.text(
        (left, 16),
        f"{idea.instrument_id.split(':')[-1]}  H1  |  SMC + TRADE PLAN",
        fill="#F4F5F7",
        font=title_font,
    )

    def y(price: float) -> float:
        return bottom - (price - lo) / (hi - lo) * (bottom - top)

    count = len(rows)
    step = (right - left) / max(count, 1)

    def x(index: int) -> float:
        return left + (index + 0.5) * step

    for i in range(6):
        yy = top + (bottom - top) * i / 5
        price = hi - (hi - lo) * i / 5
        draw.line((left, yy, right, yy), fill="#2A2E37", width=1)
        draw.text((right + 10, yy - 8), _price(price), fill="#8E95A3", font=small_font)

    candle_width = max(2, min(9, int(step * 0.58)))
    for index, row in enumerate(rows):
        xx = x(index)
        open_price = float(row.open)
        close_price = float(row.close)
        high_price = float(row.high)
        low_price = float(row.low)
        rising = close_price >= open_price
        color = "#39C58A" if rising else "#F05D68"
        draw.line((xx, y(high_price), xx, y(low_price)), fill=color, width=2)
        y1, y2 = y(open_price), y(close_price)
        if abs(y2 - y1) < 2:
            y2 = y1 + (2 if rising else -2)
        draw.rectangle(
            (xx - candle_width / 2, min(y1, y2), xx + candle_width / 2, max(y1, y2)),
            fill=color,
        )

    def horizontal(price: float, label: str, color: str, *, width: int = 2) -> None:
        yy = y(price)
        draw.line((left, yy, right, yy), fill=color, width=width)
        box = draw.textbbox((0, 0), label, font=label_font)
        w = box[2] - box[0] + 14
        h = box[3] - box[1] + 8
        draw.rectangle((left + 4, yy - h, left + 4 + w, yy), fill="#111318E8")
        draw.text((left + 11, yy - h + 3), label, fill=color, font=label_font)

    entry_low, entry_high = float(idea.entry_low), float(idea.entry_high)
    draw.rectangle(
        (left, y(max(entry_low, entry_high)), right, y(min(entry_low, entry_high))),
        fill="#E2B71420",
        outline="#E2B714B0",
        width=2,
    )
    horizontal(float(idea.entry_reference), "ENTRY", "#E2B714", width=2)
    horizontal(float(idea.stop), "SL", "#F05D68", width=3)
    horizontal(float(idea.tp1), "TP1", "#39C58A")
    horizontal(float(idea.tp2), "TP2", "#39C58A")
    if idea.tp3 is not None:
        horizontal(float(idea.tp3), "TP3", "#39C58A")

    first_time = rows[0].open_time
    last_time = rows[-1].open_time
    total_seconds = max((last_time - first_time).total_seconds(), 1.0)

    def mark_x(raw: Any) -> float:
        try:
            from datetime import datetime

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
            draw.rectangle(
                (x1, y(max(low_f, high_f)), x2, y(min(low_f, high_f))),
                fill="#6A78FF20",
                outline="#8791FFB0",
                width=2,
            )
        else:
            draw.line((x1, y(low_f), min(right, x1 + 90), y(low_f)), fill="#8791FF", width=2)
        draw.text((min(right - 70, x1 + 4), y(high_f) - 20), label, fill="#AEB5FF", font=small_font)

    draw.rectangle(_PLOT, outline="#3B404B", width=1)
    draw.text(
        (left, 632),
        f"{rows[0].open_time:%d.%m %H:%M}  →  {rows[-1].open_time:%d.%m %H:%M} UTC",
        fill="#737B89",
        font=small_font,
    )

    out = BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


__all__ = ["render_idea_chart"]

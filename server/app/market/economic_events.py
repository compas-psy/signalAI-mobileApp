"""Owned, provider-neutral economic-event calendar contract.

The calendar accepts already-fetched provider rows; transport deliberately
does not live here.  That keeps provider credentials/network retries outside
admission and makes the point-in-time contract fixture-testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import stat
from typing import Any, Iterable, Literal

EventStatus = Literal["CLEAR", "BLOCKED", "UNAVAILABLE", "AMBIGUOUS"]


def _time(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class EventProvenance:
    source: str
    provider_event_id: str
    observed_at: datetime
    tradable_at: datetime


@dataclass(frozen=True, slots=True)
class EconomicEvent:
    title: str
    scheduled_at: datetime
    impact: str
    instrument_tags: tuple[str, ...]
    provenance: EventProvenance

    @property
    def dedup_key(self) -> tuple[str, str]:
        return self.provenance.source, self.provenance.provider_event_id


@dataclass(frozen=True, slots=True)
class EventAssessment:
    status: EventStatus
    reason_code: str
    detail: str
    event: EconomicEvent | None = None

    @property
    def blocks_admission(self) -> bool:
        return self.status in ("BLOCKED", "UNAVAILABLE", "AMBIGUOUS")

    def as_json(self) -> dict[str, Any]:
        event = self.event
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "blocks_admission": self.blocks_admission,
            "event": None if event is None else {
                "title": event.title,
                "scheduled_at": event.scheduled_at.isoformat(),
                "impact": event.impact,
                "instrument_tags": list(event.instrument_tags),
                "provenance": {
                    "source": event.provenance.source,
                    "provider_event_id": event.provenance.provider_event_id,
                    "observed_at": event.provenance.observed_at.isoformat(),
                    "tradable_at": event.provenance.tradable_at.isoformat(),
                },
            },
        }


def parse_provider_rows(rows: Iterable[dict[str, object]], *, source: str) -> tuple[EconomicEvent, ...]:
    """Parse deterministic fixture/provider rows and retain first duplicate."""
    if not source.strip():
        raise ValueError("source must not be blank")
    parsed: list[EconomicEvent] = []
    for raw in rows:
        event_id = str(raw.get("provider_event_id") or "").strip()
        title = str(raw.get("title") or "").strip()
        tags = tuple(sorted({str(tag).strip().upper() for tag in raw.get("instrument_tags", []) if str(tag).strip()}))
        if not event_id or not title or not tags:
            raise ValueError("provider row requires provider_event_id, title and instrument_tags")
        observed = _time(raw.get("observed_at"), "observed_at")
        tradable = _time(raw.get("tradable_at"), "tradable_at")
        if tradable < observed:
            raise ValueError("tradable_at must not precede observed_at")
        event = EconomicEvent(
            title=title,
            scheduled_at=_time(raw.get("scheduled_at"), "scheduled_at"),
            impact=str(raw.get("impact") or "LOW").upper(),
            instrument_tags=tags,
            provenance=EventProvenance(source, event_id, observed, tradable),
        )
        parsed.append(event)
    # Revisions are intentionally retained; ``visible`` selects the latest
    # revision known at the caller's as-of instant, independently of feed order.
    return tuple(sorted(parsed, key=lambda item: (item.dedup_key, item.provenance.tradable_at, item.provenance.observed_at, item.title)))


class EconomicEventCalendar:
    """Point-in-time calendar for admission; unknown data always fails closed."""
    def __init__(self, rows: Iterable[EconomicEvent], *, source_available: bool, fetched_at: datetime | None = None, coverage_until: datetime | None = None, max_age: timedelta = timedelta(hours=6), high_impact_window: timedelta = timedelta(hours=1), source_identity: str = "") -> None:
        self._rows = tuple(rows)
        self._source_available = source_available
        self._fetched_at = fetched_at
        self._coverage_until = coverage_until
        self._max_age = max_age
        self._high_impact_window = high_impact_window
        self._source_identity = source_identity

    def visible(self, *, as_of: datetime) -> tuple[EconomicEvent, ...]:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        moment = as_of.astimezone(UTC)
        revisions: dict[tuple[str, str], EconomicEvent] = {}
        for row in self._rows:
            if row.provenance.tradable_at > moment:
                continue
            previous = revisions.get(row.dedup_key)
            if previous is None or _revision_key(row) > _revision_key(previous):
                revisions[row.dedup_key] = row
        return tuple(sorted(revisions.values(), key=lambda item: (item.scheduled_at, item.dedup_key)))

    def assess(self, instrument_id: str, *, as_of: datetime) -> EventAssessment:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        if not self._source_available:
            return EventAssessment("UNAVAILABLE", "EVENT_SOURCE_UNAVAILABLE", "календарь событий недоступен")
        if self._fetched_at is None or self._coverage_until is None:
            return EventAssessment("UNAVAILABLE", "EVENT_SOURCE_METADATA_UNAVAILABLE", "нет fetched_at или coverage_until")
        moment = as_of.astimezone(UTC)
        if moment - self._fetched_at > self._max_age:
            return EventAssessment("UNAVAILABLE", "EVENT_SOURCE_STALE", "календарь событий устарел")
        if self._coverage_until < moment + self._high_impact_window:
            return EventAssessment("UNAVAILABLE", "EVENT_SOURCE_COVERAGE_GAP", "календарь не покрывает окно решения")
        tags = _instrument_tags(instrument_id)
        if tags is None:
            return EventAssessment("AMBIGUOUS", "EVENT_INSTRUMENT_MAPPING_AMBIGUOUS", "инструмент не покрыт явной картой календаря")
        rows = self.visible(as_of=as_of)
        candidates = [row for row in rows if set(row.instrument_tags) & tags]
        global_rows = [row for row in rows if "GLOBAL" in row.instrument_tags]
        if global_rows and not candidates:
            return EventAssessment("AMBIGUOUS", "EVENT_INSTRUMENT_MAPPING_AMBIGUOUS", "событие нельзя однозначно сопоставить инструменту", global_rows[0])
        active = [row for row in candidates if row.impact == "HIGH" and abs(row.scheduled_at - moment) <= self._high_impact_window]
        if active:
            return EventAssessment("BLOCKED", "HIGH_IMPACT_EVENT_WINDOW", "окно high-impact события", active[0])
        return EventAssessment("CLEAR", "NO_BLOCKING_EVENT", "проверка календаря завершена")


def _revision_key(row: EconomicEvent) -> tuple[datetime, datetime, str, str]:
    return (row.provenance.tradable_at, row.provenance.observed_at, row.title, row.impact)


def _instrument_tags(instrument_id: str) -> set[str] | None:
    normalized = instrument_id.upper()
    symbol = normalized.rsplit(":", 1)[-1]
    # FORTS contracts are conservatively sensitive to the USD/RUB macro lane.
    # Si and Brent keep their more specific mappings; unknown venues are never
    # guessed and remain fail-closed.
    if symbol.startswith("SI"):
        return {"USD", "RUB"}
    if symbol.startswith("BR"):
        return {"OIL", "USD"}
    if normalized.startswith("MOEX:FUT:"):
        return {"USD", "RUB"}
    if symbol.endswith("USDT"):
        return {"CRYPTO"}
    return None


def load_owned_calendar(*, now: datetime, path: str | None = None, max_age: timedelta = timedelta(hours=6), high_impact_window: timedelta = timedelta(hours=1)) -> EconomicEventCalendar:
    """Load an owner-configured absolute JSON snapshot; all failures fail closed."""
    configured = path or os.environ.get("SIGNALAI_EVENT_CALENDAR_PATH", "")
    file = Path(configured)
    if not configured or not file.is_absolute():
        return EconomicEventCalendar((), source_available=False)
    try:
        payload, stat_result = _read_owned_snapshot(file)
        raw = json.loads(payload)
        if not isinstance(raw, dict) or not isinstance(raw.get("events"), list):
            raise ValueError("invalid calendar envelope")
        fetched_at = _time(raw.get("fetched_at"), "fetched_at")
        coverage_until = _time(raw.get("coverage_until"), "coverage_until")
        source = str(raw.get("source") or "").strip()
        rows = parse_provider_rows(raw["events"], source=source)
        identity = f"{file}:{stat_result.st_mtime_ns}:{source}"
        return EconomicEventCalendar(rows, source_available=True, fetched_at=fetched_at, coverage_until=coverage_until, max_age=max_age, high_impact_window=high_impact_window, source_identity=identity)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return EconomicEventCalendar((), source_available=False)


def _read_owned_snapshot(file: Path) -> tuple[str, os.stat_result]:
    """Read one regular file through a descriptor-pinned, symlink-free path."""
    if (
        not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or os.open not in os.supports_dir_fd
        or not file.anchor
        or len(file.parts) < 2
        or any(part in {"", ".", ".."} for part in file.parts[1:])
    ):
        raise OSError("secure calendar file open is unavailable")

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
        file_flags |= os.O_CLOEXEC

    parent_fd: int | None = None
    snapshot_fd: int | None = None
    try:
        parent_fd = os.open(file.anchor, directory_flags)
        for component in file.parts[1:-1]:
            child_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = child_fd
        snapshot_fd = os.open(file.name, file_flags, dir_fd=parent_fd)
        stat_result = os.fstat(snapshot_fd)
        if not stat.S_ISREG(stat_result.st_mode):
            raise OSError("calendar snapshot is not a regular file")
        with os.fdopen(snapshot_fd, "r", encoding="utf-8") as stream:
            snapshot_fd = None
            return stream.read(), stat_result
    finally:
        if snapshot_fd is not None:
            os.close(snapshot_fd)
        if parent_fd is not None:
            os.close(parent_fd)

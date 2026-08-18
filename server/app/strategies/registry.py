"""Audited strategy registry for champion/challenger governance.

The registry is deliberately not wired into the production scan/admission or
paper lifecycle paths.  It answers measurement/governance questions and makes
role changes auditable; it is not a runtime kill/enable switch.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import StrategyPromotionEvent, StrategyVersion
from ..models.enums import Strategy
from .versioning import (
    LEGACY_CONTROL_VERSION,
    StrategyRole,
    TradingStage,
)


@dataclass(frozen=True, slots=True)
class StrategyDescriptor:
    family: str
    version: str
    role: StrategyRole
    enabled_stages: frozenset[TradingStage]
    config_hash: str


@dataclass(frozen=True, slots=True)
class StrategyRegistryState:
    descriptor: StrategyDescriptor
    ui_visible: bool

    @property
    def role(self) -> StrategyRole:
        return self.descriptor.role


class StrategyRegistry:
    """DB-backed version registry with append-only state transitions."""

    def __init__(self, session: Session):
        self.session = session

    def _row(self, family: str, version: str) -> StrategyVersion:
        row = self.session.execute(
            select(StrategyVersion).where(
                StrategyVersion.family == family,
                StrategyVersion.version == version,
            )
        ).scalar_one_or_none()
        if row is None:
            raise KeyError(f"strategy version not registered: {family}/{version}")
        return row

    @staticmethod
    def _latest(row: StrategyVersion) -> StrategyPromotionEvent:
        if not row.events:
            raise RuntimeError(
                f"strategy version has no registry history: {row.family}/{row.version}"
            )
        return row.events[-1]

    @staticmethod
    def _descriptor(row: StrategyVersion, event: StrategyPromotionEvent) -> StrategyDescriptor:
        return StrategyDescriptor(
            family=row.family,
            version=row.version,
            role=StrategyRole(event.to_role),
            enabled_stages=frozenset(TradingStage(stage) for stage in event.enabled_stages),
            config_hash=row.config_hash,
        )

    @staticmethod
    def _matches_scope(row: StrategyVersion, *, venue: str, instrument: str) -> bool:
        if row.venue_allowlist and venue not in row.venue_allowlist:
            return False
        if row.instrument_prefixes and not any(
            instrument.startswith(prefix) for prefix in row.instrument_prefixes
        ):
            return False
        return True

    def get(self, family: str, version: str) -> StrategyDescriptor:
        row = self._row(family, version)
        return self._descriptor(row, self._latest(row))

    def version_row(self, family: str, version: str) -> StrategyVersion:
        return self._row(family, version)

    def history(self, family: str, version: str) -> tuple[StrategyPromotionEvent, ...]:
        row = self._row(family, version)
        return tuple(row.events)

    def register(
        self,
        descriptor: StrategyDescriptor,
        *,
        actor: str,
        reason: str,
        venues: frozenset[str] = frozenset(),
        instrument_prefixes: frozenset[str] = frozenset(),
    ) -> StrategyDescriptor:
        if not actor.strip():
            raise ValueError("actor is required")
        if not reason.strip():
            raise ValueError("registration reason is required")
        if not descriptor.enabled_stages:
            raise ValueError("enabled_stages must not be empty")
        if len(descriptor.config_hash) != 64:
            raise ValueError("config_hash must be a 64-character digest")
        existing = self.session.execute(
            select(StrategyVersion.id).where(
                StrategyVersion.family == descriptor.family,
                StrategyVersion.version == descriptor.version,
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError(
                f"strategy version already registered: {descriptor.family}/{descriptor.version}"
            )

        row = StrategyVersion(
            family=descriptor.family,
            version=descriptor.version,
            config_hash=descriptor.config_hash,
            venue_allowlist=sorted(venues),
            instrument_prefixes=sorted(instrument_prefixes),
        )
        self.session.add(row)
        self.session.flush()
        event = StrategyPromotionEvent(
            strategy_version_id=row.id,
            sequence=1,
            actor=actor,
            event_type="REGISTERED",
            from_role=None,
            to_role=descriptor.role.value,
            enabled_stages=sorted(stage.value for stage in descriptor.enabled_stages),
            ui_visible=True,
            decision_ref=None,
            reason=reason,
            detail_json={},
        )
        self.session.add(event)
        self.session.flush()
        self.session.refresh(row)
        return self.get(row.family, row.version)

    def _append_state(
        self,
        row: StrategyVersion,
        *,
        actor: str,
        event_type: str,
        to_role: StrategyRole,
        enabled_stages: frozenset[TradingStage],
        ui_visible: bool,
        decision_ref: str | None,
        reason: str,
    ) -> StrategyRegistryState:
        if not actor.strip():
            raise ValueError("actor is required")
        if not reason.strip():
            raise ValueError("reason is required")
        if not enabled_stages:
            raise ValueError("enabled_stages must not be empty")
        current = self._latest(row)
        event = StrategyPromotionEvent(
            strategy_version_id=row.id,
            sequence=current.sequence + 1,
            actor=actor,
            event_type=event_type,
            from_role=current.to_role,
            to_role=to_role.value,
            enabled_stages=sorted(stage.value for stage in enabled_stages),
            ui_visible=ui_visible,
            decision_ref=decision_ref or None,
            reason=reason,
            detail_json={},
        )
        self.session.add(event)
        self.session.flush()
        self.session.refresh(row)
        latest = self._latest(row)
        return StrategyRegistryState(
            descriptor=self._descriptor(row, latest),
            ui_visible=latest.ui_visible,
        )

    def record_promotion(
        self,
        family: str,
        version: str,
        *,
        to_role: StrategyRole,
        actor: str,
        decision_ref: str,
        reason: str,
        enabled_stages: frozenset[TradingStage] | None = None,
    ) -> StrategyDescriptor:
        row = self._row(family, version)
        current = self._latest(row)
        current_role = StrategyRole(current.to_role)

        if version == LEGACY_CONTROL_VERSION and family in {item.value for item in Strategy}:
            if to_role is not StrategyRole.CONTROL:
                raise ValueError("legacy control role is immutable in SAI-003")
        if to_role is StrategyRole.CHAMPION and not decision_ref.strip():
            raise ValueError("decision_ref is required to promote a strategy to CHAMPION")

        stages = enabled_stages or frozenset(
            TradingStage(stage) for stage in current.enabled_stages
        )
        state = self._append_state(
            row,
            actor=actor,
            event_type="ROLE_CHANGED" if to_role is not current_role else "ROLE_CONFIRMED",
            to_role=to_role,
            enabled_stages=stages,
            ui_visible=current.ui_visible,
            decision_ref=decision_ref,
            reason=reason,
        )
        return state.descriptor

    def set_ui_visibility(
        self,
        family: str,
        version: str,
        *,
        visible: bool,
        actor: str,
        reason: str,
    ) -> StrategyRegistryState:
        row = self._row(family, version)
        current = self._latest(row)
        return self._append_state(
            row,
            actor=actor,
            event_type="UI_VISIBILITY_CHANGED",
            to_role=StrategyRole(current.to_role),
            enabled_stages=frozenset(
                TradingStage(stage) for stage in current.enabled_stages
            ),
            ui_visible=visible,
            decision_ref=None,
            reason=reason,
        )

    def _active(
        self,
        *,
        stage: TradingStage,
        venue: str,
        instrument: str,
        ui_only: bool,
    ) -> tuple[StrategyDescriptor, ...]:
        rows = self.session.execute(
            select(StrategyVersion).order_by(StrategyVersion.family, StrategyVersion.version)
        ).scalars().all()
        result: list[StrategyDescriptor] = []
        for row in rows:
            event = self._latest(row)
            if ui_only and not event.ui_visible:
                continue
            if stage.value not in event.enabled_stages:
                continue
            if not self._matches_scope(row, venue=venue, instrument=instrument):
                continue
            result.append(self._descriptor(row, event))
        return tuple(result)

    def active_for(
        self, *, stage: TradingStage, venue: str, instrument: str
    ) -> tuple[StrategyDescriptor, ...]:
        """Return governance-active versions; UI visibility does not disable them."""

        return self._active(stage=stage, venue=venue, instrument=instrument, ui_only=False)

    def visible_for_ui(
        self, *, stage: TradingStage, venue: str, instrument: str
    ) -> tuple[StrategyDescriptor, ...]:
        return self._active(stage=stage, venue=venue, instrument=instrument, ui_only=True)

    def for_replay(self, family: str, version: str) -> StrategyDescriptor:
        """Historical experiment replay ignores UI visibility by design."""

        return self.get(family, version)


__all__ = ["StrategyDescriptor", "StrategyRegistry", "StrategyRegistryState"]

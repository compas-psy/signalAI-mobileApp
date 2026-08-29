"""Content-addressed dataset snapshots and point-in-time replay.

The same resolver is used by live and backtest callers. Snapshot selection is
based only on persisted ``tradable_at`` boundaries; artifacts are verified by
SHA-256 before any rows are returned. Historical research may additionally pin
an exact ``snapshot_id`` so a persisted backtest is byte-for-byte reproducible.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DatasetSnapshot


class SnapshotIntegrityError(RuntimeError):
    """Raised when persisted metadata and artifact bytes do not agree."""


@dataclass(frozen=True, slots=True)
class DatasetRow:
    key: str
    tradable_at: datetime
    values: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("dataset row key is required")
        if self.tradable_at.tzinfo is None:
            raise ValueError("dataset row tradable_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    dataset_name: str
    dataset_version: str
    schema_version: str
    snapshot_id: str
    tradable_at: datetime
    source_watermark: dict[str, Any]
    row_count: int
    content_sha256: str
    manifest_sha256: str
    artifact_key: str
    rows: tuple[DatasetRow, ...]
    artifact_bytes: bytes


@dataclass(frozen=True, slots=True)
class ResolvedDataset:
    dataset_name: str
    dataset_version: str
    schema_version: str
    snapshot_id: str
    tradable_at: datetime
    source_watermark: dict[str, Any]
    row_count: int
    content_sha256: str
    manifest_sha256: str
    rows: tuple[DatasetRow, ...]

    @property
    def audit(self) -> dict[str, Any]:
        return {
            "dataset_name": self.dataset_name,
            "dataset_version": self.dataset_version,
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "content_sha256": self.content_sha256,
            "manifest_sha256": self.manifest_sha256,
            "source_watermark": self.source_watermark,
            "row_count": self.row_count,
            "tradable_at": self.tradable_at.isoformat(),
        }


def _normalise(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _normalise(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_normalise(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported snapshot value type: {type(value).__name__}")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _normalise(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_dataset_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")
    if not safe:
        raise ValueError("dataset_name must contain a filesystem-safe character")
    return safe


class DatasetSnapshotBuilder:
    @staticmethod
    def build(
        *,
        dataset_name: str,
        dataset_version: str,
        schema_version: str,
        tradable_at: datetime,
        source_watermark: dict[str, Any],
        rows: Iterable[DatasetRow],
    ) -> SnapshotManifest:
        if not dataset_name.strip():
            raise ValueError("dataset_name is required")
        if not dataset_version.strip():
            raise ValueError("dataset_version is required")
        if not schema_version.strip():
            raise ValueError("schema_version is required")
        if tradable_at.tzinfo is None:
            raise ValueError("snapshot tradable_at must be timezone-aware")

        ordered = tuple(sorted(rows, key=lambda row: row.key))
        keys = [row.key for row in ordered]
        if len(keys) != len(set(keys)):
            raise ValueError("dataset row keys must be unique inside a snapshot")
        for row in ordered:
            if row.tradable_at > tradable_at:
                raise ValueError(
                    f"future row {row.key!r} has tradable_at after snapshot boundary"
                )

        artifact_payload = {
            "rows": [
                {
                    "key": row.key,
                    "tradable_at": row.tradable_at.isoformat(),
                    "values": row.values,
                }
                for row in ordered
            ]
        }
        artifact_bytes = _canonical_bytes(artifact_payload)
        content_sha256 = _sha256(artifact_bytes)
        manifest_payload = {
            "dataset_name": dataset_name,
            "dataset_version": dataset_version,
            "schema_version": schema_version,
            "tradable_at": tradable_at.isoformat(),
            "source_watermark": source_watermark,
            "row_count": len(ordered),
            "content_sha256": content_sha256,
        }
        manifest_sha256 = _sha256(_canonical_bytes(manifest_payload))
        snapshot_id = manifest_sha256
        artifact_key = f"{_safe_dataset_name(dataset_name)}/{snapshot_id}.json"

        return SnapshotManifest(
            dataset_name=dataset_name,
            dataset_version=dataset_version,
            schema_version=schema_version,
            snapshot_id=snapshot_id,
            tradable_at=tradable_at,
            source_watermark=_normalise(source_watermark),
            row_count=len(ordered),
            content_sha256=content_sha256,
            manifest_sha256=manifest_sha256,
            artifact_key=artifact_key,
            rows=ordered,
            artifact_bytes=artifact_bytes,
        )


class FilesystemSnapshotStore:
    """Atomic content-addressed artifact store for one filesystem root.

    Production may mount this root on durable object-backed storage. The API is
    intentionally tiny so another immutable backend can implement the same
    ``publish/read_bytes`` contract without changing the resolver.
    """

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, artifact_key: str) -> Path:
        candidate = (self.root / artifact_key).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("artifact key escapes snapshot root")
        return candidate

    def publish(self, artifact_key: str, data: bytes) -> None:
        target = self._path(artifact_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing = target.read_bytes()
            if existing != data:
                raise SnapshotIntegrityError(
                    "immutable artifact key already exists with different content"
                )
            return

        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temp_name, target)
            except FileExistsError:
                existing = target.read_bytes()
                if existing != data:
                    raise SnapshotIntegrityError(
                        "immutable artifact key already exists with different content"
                    )
            finally:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise

    def read_bytes(self, artifact_key: str) -> bytes:
        return self._path(artifact_key).read_bytes()


def _same_manifest(row: DatasetSnapshot, manifest: SnapshotManifest) -> bool:
    return all(
        (
            row.dataset_version == manifest.dataset_version,
            row.schema_version == manifest.schema_version,
            row.tradable_at == manifest.tradable_at,
            row.source_watermark == manifest.source_watermark,
            row.row_count == manifest.row_count,
            row.content_sha256 == manifest.content_sha256,
            row.manifest_sha256 == manifest.manifest_sha256,
            row.artifact_key == manifest.artifact_key,
        )
    )


def publish_snapshot(
    session: Session,
    *,
    store: FilesystemSnapshotStore,
    manifest: SnapshotManifest,
) -> DatasetSnapshot:
    """Publish artifact first, then append its immutable manifest idempotently."""

    store.publish(manifest.artifact_key, manifest.artifact_bytes)
    existing = session.execute(
        select(DatasetSnapshot).where(
            DatasetSnapshot.dataset_name == manifest.dataset_name,
            DatasetSnapshot.snapshot_id == manifest.snapshot_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if not _same_manifest(existing, manifest):
            raise SnapshotIntegrityError(
                "persisted snapshot identity conflicts with supplied manifest"
            )
        return existing

    row = DatasetSnapshot(
        dataset_name=manifest.dataset_name,
        dataset_version=manifest.dataset_version,
        schema_version=manifest.schema_version,
        snapshot_id=manifest.snapshot_id,
        tradable_at=manifest.tradable_at,
        source_watermark=manifest.source_watermark,
        row_count=manifest.row_count,
        content_sha256=manifest.content_sha256,
        manifest_sha256=manifest.manifest_sha256,
        artifact_key=manifest.artifact_key,
    )
    session.add(row)
    session.flush()
    return row


def _rows_from_artifact(data: bytes) -> tuple[DatasetRow, ...]:
    payload = json.loads(data.decode("utf-8"))
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list):
        raise SnapshotIntegrityError("snapshot artifact rows are missing")
    rows: list[DatasetRow] = []
    for item in raw_rows:
        if not isinstance(item, dict):
            raise SnapshotIntegrityError("snapshot artifact row is invalid")
        try:
            rows.append(
                DatasetRow(
                    key=str(item["key"]),
                    tradable_at=datetime.fromisoformat(str(item["tradable_at"])),
                    values=dict(item["values"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SnapshotIntegrityError("snapshot artifact row is invalid") from exc
    return tuple(rows)


def _manifest_hash(row: DatasetSnapshot) -> str:
    return _sha256(
        _canonical_bytes(
            {
                "dataset_name": row.dataset_name,
                "dataset_version": row.dataset_version,
                "schema_version": row.schema_version,
                "tradable_at": row.tradable_at.isoformat(),
                "source_watermark": row.source_watermark,
                "row_count": row.row_count,
                "content_sha256": row.content_sha256,
            }
        )
    )


class DatasetSnapshotResolver:
    """Single integrity-checking resolver shared by live and research paths."""

    def __init__(self, session: Session, *, store: FilesystemSnapshotStore):
        self.session = session
        self.store = store

    def _manifest_row(self, dataset_name: str, decision_time: datetime) -> DatasetSnapshot:
        if decision_time.tzinfo is None:
            raise ValueError("decision_time must be timezone-aware")
        row = self.session.execute(
            select(DatasetSnapshot)
            .where(
                DatasetSnapshot.dataset_name == dataset_name,
                DatasetSnapshot.tradable_at <= decision_time,
            )
            .order_by(
                DatasetSnapshot.tradable_at.desc(),
                DatasetSnapshot.created_at.desc(),
                DatasetSnapshot.snapshot_id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            raise KeyError(
                f"no snapshot for {dataset_name!r} at or before {decision_time.isoformat()}"
            )
        return row

    def _exact_manifest_row(self, snapshot_id: str) -> DatasetSnapshot:
        identity = snapshot_id.strip()
        if len(identity) != 64:
            raise ValueError("snapshot_id must be a 64-character digest")
        row = self.session.execute(
            select(DatasetSnapshot).where(DatasetSnapshot.snapshot_id == identity)
        ).scalar_one_or_none()
        if row is None:
            raise KeyError(f"no dataset snapshot with id {identity!r}")
        return row

    def _resolve_row(
        self,
        row: DatasetSnapshot,
        *,
        decision_time: datetime,
    ) -> ResolvedDataset:
        if decision_time.tzinfo is None:
            raise ValueError("decision_time must be timezone-aware")
        if row.tradable_at > decision_time:
            raise SnapshotIntegrityError("snapshot is not tradable at decision time")

        data = self.store.read_bytes(row.artifact_key)
        if _sha256(data) != row.content_sha256:
            raise SnapshotIntegrityError("snapshot content checksum mismatch")
        if _manifest_hash(row) != row.manifest_sha256:
            raise SnapshotIntegrityError("snapshot manifest checksum mismatch")
        if row.snapshot_id != row.manifest_sha256:
            raise SnapshotIntegrityError("snapshot identity checksum mismatch")

        rows = _rows_from_artifact(data)
        if len(rows) != row.row_count:
            raise SnapshotIntegrityError("snapshot row count mismatch")
        if any(item.tradable_at > decision_time for item in rows):
            raise SnapshotIntegrityError("snapshot contains a future row for decision time")
        if any(item.tradable_at > row.tradable_at for item in rows):
            raise SnapshotIntegrityError("snapshot contains a row after its tradable_at")

        return ResolvedDataset(
            dataset_name=row.dataset_name,
            dataset_version=row.dataset_version,
            schema_version=row.schema_version,
            snapshot_id=row.snapshot_id,
            tradable_at=row.tradable_at,
            source_watermark=dict(row.source_watermark),
            row_count=row.row_count,
            content_sha256=row.content_sha256,
            manifest_sha256=row.manifest_sha256,
            rows=rows,
        )

    def resolve(self, dataset_name: str, *, decision_time: datetime) -> ResolvedDataset:
        row = self._manifest_row(dataset_name, decision_time)
        return self._resolve_row(row, decision_time=decision_time)

    def resolve_snapshot_id(self, snapshot_id: str) -> ResolvedDataset:
        """Resolve exactly one immutable identity, never 'latest as of' data."""

        row = self._exact_manifest_row(snapshot_id)
        return self._resolve_row(row, decision_time=row.tradable_at)

    def resolve_live(self, dataset_name: str, *, decision_time: datetime) -> ResolvedDataset:
        return self.resolve(dataset_name, decision_time=decision_time)

    def resolve_backtest(
        self, dataset_name: str, *, decision_time: datetime
    ) -> ResolvedDataset:
        return self.resolve(dataset_name, decision_time=decision_time)

    def replay(self, dataset_name: str, *, decision_time: datetime) -> ResolvedDataset:
        return self.resolve(dataset_name, decision_time=decision_time)


__all__ = [
    "DatasetRow",
    "DatasetSnapshotBuilder",
    "DatasetSnapshotResolver",
    "FilesystemSnapshotStore",
    "ResolvedDataset",
    "SnapshotIntegrityError",
    "SnapshotManifest",
    "publish_snapshot",
]

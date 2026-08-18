"""Read-only operator CLI for audited point-in-time dataset replay.

Usage example::

    python -m app.datasets.replay_cli short_horizon_features \
        --decision-time 2026-08-18T09:01:00+00:00 \
        --root /var/lib/signalai/dataset-snapshots

The CLI delegates all selection and integrity checks to ``DatasetSnapshotResolver``;
it deliberately contains no independent snapshot-selection logic.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Sequence, TextIO

from sqlalchemy.orm import Session

from ..db import session_scope
from .snapshots import DatasetSnapshotResolver, FilesystemSnapshotStore, ResolvedDataset


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signalai-dataset-replay",
        description="Replay the exact immutable dataset available at a decision time.",
    )
    parser.add_argument("dataset_name")
    parser.add_argument(
        "--decision-time",
        required=True,
        help="Timezone-aware ISO-8601 decision time, for example 2026-08-18T09:01:00+00:00",
    )
    parser.add_argument(
        "--root",
        default=os.environ.get("SIGNALAI_SNAPSHOT_ROOT", ""),
        help="Immutable snapshot artifact root (or SIGNALAI_SNAPSHOT_ROOT).",
    )
    return parser


def _parse_decision_time(parser: argparse.ArgumentParser, raw: str) -> datetime:
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        parser.error("--decision-time must be ISO-8601")
    if value.tzinfo is None:
        parser.error("--decision-time must be timezone-aware")
    return value


def _payload(result: ResolvedDataset) -> dict:
    return {
        "audit": result.audit,
        "rows": [
            {
                "key": row.key,
                "tradable_at": row.tradable_at.isoformat(),
                "values": row.values,
            }
            for row in result.rows
        ],
    }


def _run(
    *,
    session: Session,
    root: str,
    dataset_name: str,
    decision_time: datetime,
) -> ResolvedDataset:
    store = FilesystemSnapshotStore(root)
    return DatasetSnapshotResolver(session, store=store).replay(
        dataset_name,
        decision_time=decision_time,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    session: Session | None = None,
    stdout: TextIO | None = None,
) -> int:
    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.root.strip():
        parser.error("--root or SIGNALAI_SNAPSHOT_ROOT is required")
    decision_time = _parse_decision_time(parser, args.decision_time)

    if session is not None:
        result = _run(
            session=session,
            root=args.root,
            dataset_name=args.dataset_name,
            decision_time=decision_time,
        )
    else:
        with session_scope() as db:
            result = _run(
                session=db,
                root=args.root,
                dataset_name=args.dataset_name,
                decision_time=decision_time,
            )

    stream = stdout or sys.stdout
    json.dump(_payload(result), stream, ensure_ascii=False, sort_keys=True)
    stream.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

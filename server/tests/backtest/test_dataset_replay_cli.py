from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta

from app.datasets.replay_cli import main
from app.datasets.snapshots import (
    DatasetRow,
    DatasetSnapshotBuilder,
    FilesystemSnapshotStore,
    publish_snapshot,
)


BASE = datetime(2026, 8, 18, 9, tzinfo=UTC)


def test_replay_cli_outputs_auditable_snapshot_json(session, tmp_path):
    manifest = DatasetSnapshotBuilder.build(
        dataset_name="short_horizon_features",
        dataset_version="features_v1",
        schema_version="1",
        tradable_at=BASE,
        source_watermark={"market_bars": "wm-cli"},
        rows=(
            DatasetRow(
                key="BTCUSDT:1h:2026-08-18T08:00:00Z",
                tradable_at=BASE,
                values={"close": "118000.5"},
            ),
        ),
    )
    store = FilesystemSnapshotStore(tmp_path)
    publish_snapshot(session, store=store, manifest=manifest)
    session.flush()
    output = io.StringIO()

    code = main(
        [
            "short_horizon_features",
            "--decision-time",
            (BASE + timedelta(minutes=1)).isoformat(),
            "--root",
            str(tmp_path),
        ],
        session=session,
        stdout=output,
    )

    assert code == 0
    payload = json.loads(output.getvalue())
    assert payload["audit"]["snapshot_id"] == manifest.snapshot_id
    assert payload["audit"]["source_watermark"] == {"market_bars": "wm-cli"}
    assert payload["rows"] == [
        {
            "key": "BTCUSDT:1h:2026-08-18T08:00:00Z",
            "tradable_at": BASE.isoformat(),
            "values": {"close": "118000.5"},
        }
    ]

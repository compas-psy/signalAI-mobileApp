from __future__ import annotations

from pathlib import Path

import yaml


def test_bybit_research_snapshot_volume_is_durable_and_heavy_lane_only() -> None:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert "dataset-snapshots" in compose["volumes"]
    assert "dataset-volume-init" in services

    init = services["dataset-volume-init"]
    api = services["api"]
    heavy = services["scheduler-heavy"]
    market = services["scheduler"]

    assert init["image"] == "signalai-scheduler"
    assert init["user"] == "0:0"
    init_command = " ".join(str(item) for item in init["command"])
    assert "chown signalai:signalai /var/lib/signalai-datasets" in init_command
    assert "10001" not in init_command

    assert api["environment"]["SIGNALAI_DATASET_SNAPSHOT_ROOT"] == "/var/lib/signalai-datasets"
    assert "dataset-snapshots:/var/lib/signalai-datasets:ro" in api["volumes"]

    assert heavy["environment"]["SIGNALAI_SCHEDULER_LANE"] == "heavy"
    assert heavy["environment"]["SIGNALAI_DATASET_SNAPSHOT_ROOT"] == "/var/lib/signalai-datasets"
    assert "dataset-snapshots:/var/lib/signalai-datasets" in heavy["volumes"]
    assert heavy["depends_on"]["dataset-volume-init"]["condition"] == "service_completed_successfully"

    assert market["environment"]["SIGNALAI_SCHEDULER_LANE"] == "market"
    assert all("dataset-snapshots" not in mount for mount in market.get("volumes", []))

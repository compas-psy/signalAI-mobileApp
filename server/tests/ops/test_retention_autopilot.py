from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.ops.retention import RetentionAutopilotConfig
from app.scheduler.runner import build_default_scheduler


def test_owner_config_requires_explicit_enabled_marked_absolute_target_and_stays_dry_run():
    config = RetentionAutopilotConfig.from_mapping(
        {
            "enabled": True,
            "dry_run": True,
            "targets": [
                {
                    "root": "/srv/signalai/retention/cache",
                    "min_age_hours": 168,
                    "max_delete_files": 10,
                    "max_delete_bytes": 4096,
                }
            ],
        }
    )

    assert config.enabled is True
    assert config.dry_run is True
    assert config.targets[0].root == Path("/srv/signalai/retention/cache")
    assert config.targets[0].min_age == timedelta(days=7)


@pytest.mark.parametrize(
    "raw",
    [
        {"enabled": True, "targets": []},
        {"enabled": True, "targets": [{"root": "relative/cache"}]},
        {"enabled": True, "dry_run": False, "targets": [{"root": "/"}]},
    ],
)
def test_owner_config_rejects_incomplete_or_dangerous_authorization(raw):
    with pytest.raises(ValueError):
        RetentionAutopilotConfig.from_mapping(raw)


def test_config_is_disabled_and_dry_run_by_default():
    config = RetentionAutopilotConfig()

    assert config.enabled is False
    assert config.dry_run is True
    assert config.targets == ()
    assert config.budget_period == timedelta(days=1)


def test_owner_budget_period_is_bounded_and_explicit():
    config = RetentionAutopilotConfig.from_mapping(
        {
            "enabled": True,
            "budget_period_hours": 6,
            "targets": [{"root": "/srv/signalai/retention/cache"}],
        }
    )

    assert config.budget_period == timedelta(hours=6)

    with pytest.raises(ValueError):
        RetentionAutopilotConfig.from_mapping(
            {
                "enabled": True,
                "budget_period_hours": 0,
                "targets": [{"root": "/srv/signalai/retention/cache"}],
            }
        )


def test_scheduler_registers_autopilot_only_after_typed_owner_enablement():
    disabled = build_default_scheduler(retention_config=RetentionAutopilotConfig())
    enabled = build_default_scheduler(
        retention_config=RetentionAutopilotConfig.from_mapping(
            {"enabled": True, "targets": [{"root": "/srv/signalai/retention/cache"}]}
        )
    )

    assert "resource-autopilot" not in [job.name for job in disabled.jobs]
    assert "resource-autopilot" in [job.name for job in enabled.jobs]

from pathlib import Path

import yaml


COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker-compose.yml"
DOCKERFILE_PATH = Path(__file__).resolve().parents[2] / "Dockerfile"
CALENDAR_MOUNT = "calendar-data:/var/lib/signalai-calendar"
ROOT_USERS = {"0", "0:0", "root"}


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_calendar_volume_has_one_shot_root_initializer() -> None:
    compose = _compose()
    services = compose["services"]

    init = services["event-calendar-volume-init"]
    assert str(init["user"]) == "0:0"
    assert CALENDAR_MOUNT in init["volumes"]

    command = " ".join(init["command"]) if isinstance(init["command"], list) else str(init["command"])
    assert "chown" in command
    assert "signalai:signalai" in command
    assert "chmod" in command
    assert "0750" in command

    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "adduser --system --group --home /srv signalai" in dockerfile
    assert "USER signalai" in dockerfile

    explicit_root_services = {
        name
        for name, service in services.items()
        if str(service.get("user", "")) in ROOT_USERS
    }
    assert explicit_root_services == {"event-calendar-volume-init"}


def test_event_calendar_waits_for_initializer_and_stays_non_root() -> None:
    services = _compose()["services"]
    calendar = services["event-calendar"]

    dependency = calendar["depends_on"]["event-calendar-volume-init"]
    assert dependency["condition"] == "service_completed_successfully"
    assert str(calendar.get("user", "")) not in ROOT_USERS
    assert CALENDAR_MOUNT in calendar["volumes"]


def test_market_scheduler_starts_owned_calendar_on_fresh_bootstrap() -> None:
    services = _compose()["services"]

    dependency = services["scheduler"]["depends_on"]["event-calendar"]
    assert dependency["condition"] == "service_started"

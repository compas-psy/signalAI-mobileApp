from pathlib import Path

import yaml


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def _workflow_text() -> str:
    return (_root() / ".github/workflows/runtime-calendar.yml").read_text(
        encoding="utf-8"
    )


def _workflow() -> dict:
    return yaml.safe_load(_workflow_text())


def test_runtime_calendar_is_owner_only_exact_issue_comment_workflow() -> None:
    workflow = _workflow()
    text = _workflow_text()

    trigger = workflow.get("on", workflow.get(True))
    assert trigger == {"issue_comment": {"types": ["created"]}}
    assert workflow["permissions"] == {"contents": "read", "issues": "write"}
    assert "github.event.issue.number == 1" in text
    assert "github.event.issue.pull_request == null" in text
    assert "github.event.comment.body == '/runtime-calendar'" in text
    assert "github.event.comment.user.login == github.repository_owner" in text
    assert workflow["jobs"]["calendar"]["timeout-minutes"] <= 5


def test_runtime_calendar_uses_pinned_ssh_and_current_compose_read_only() -> None:
    text = _workflow_text()

    assert "secrets.VPS_SSH_HOST_KEY_SHA256 || 'SHA256:cUfeWKyHoFIhKsV17Bpk076calxUaxd90XhuETb4vng'" in text
    assert "prepare_known_host.sh \"$HOST\" \"$HOST_KEY_SHA256\"" in text
    assert 'docker compose --env-file /etc/signalai/.env -f "$HOME/signalai/server/docker-compose.yml"' in text
    assert 'ps event-calendar api scheduler' in text
    assert 'logs --since 30h --tail 600 event-calendar' in text
    assert "refresh_owned_snapshot" not in text
    assert "NamedTemporaryFile" not in text
    assert "os.replace" not in text
    assert "write_text" not in text


def test_runtime_calendar_has_connect_and_per_command_time_bounds() -> None:
    text = _workflow_text()

    assert "-o ConnectTimeout=10" in text
    assert "timeout --signal=TERM --kill-after=15s 240s ssh" in text
    assert "SUDO=(sudo -n)" in text
    assert 'timeout 20s "${COMPOSE[@]}" ps event-calendar api scheduler' in text
    assert 'timeout 30s "${COMPOSE[@]}" logs --since 30h --tail 600 event-calendar' in text
    assert text.count('timeout 30s "${COMPOSE[@]}" exec -T event-calendar') == 2
    assert 'timeout 30s "${COMPOSE[@]}" exec -T api' in text
    assert 'exit "$diagnostic_failures"' in text


def test_runtime_calendar_contains_safe_snapshot_probe_provider_probe_and_api_assess() -> None:
    text = _workflow_text()

    for marker in (
        "CALENDAR_SNAPSHOT_PATH_EXISTS",
        "CALENDAR_SNAPSHOT_REGULAR_FILE",
        "CALENDAR_SNAPSHOT_SIZE",
        "CALENDAR_SNAPSHOT_MODE",
        "CALENDAR_SNAPSHOT_SOURCE",
        "CALENDAR_SNAPSHOT_FETCHED_AT",
        "CALENDAR_SNAPSHOT_COVERAGE_UNTIL",
        "CALENDAR_SNAPSHOT_EVENTS_COUNT",
        "CALENDAR_PROVIDER_PROBE=success",
        "CALENDAR_PROVIDER_PROBE=exception",
        "CALENDAR_API_ASSESSMENT",
        "fetch_xoomar_calendar",
        "load_owned_calendar",
        "CRYPTO:PERP:BTCUSDT",
    ):
        assert marker in text

    assert "--since 30h" in text
    assert "--tail 600" in text


def test_runtime_calendar_always_publishes_pointer_and_removes_ssh_key() -> None:
    workflow = _workflow()
    text = _workflow_text()

    steps = workflow["jobs"]["calendar"]["steps"]
    names = [step.get("name") for step in steps]
    assert "Publish run pointer" in names
    assert "Remove SSH key" in names
    assert text.count("if: always()") >= 2
    assert "repos/$REPO/issues/1/comments" in text
    assert "shred -u ~/.ssh/id_deploy" in text

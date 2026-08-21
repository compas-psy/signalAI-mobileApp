from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def _workflow() -> str:
    return (_root() / ".github/workflows/runtime-release-command.yml").read_text()


def _server_workflow() -> str:
    return (_root() / ".github/workflows/deploy-server.yml").read_text()


def _release_workflow() -> str:
    return (_root() / ".github/workflows/deploy-release.yml").read_text()


def test_owner_runtime_logs_command_dispatches_read_only_server_logs() -> None:
    workflow = _workflow()

    assert "'/runtime-logs'" in workflow
    assert "workflow_id: 'deploy-server.yml'" in workflow
    assert "action: 'logs'" in workflow
    assert "source_ref: sourceSha" in workflow


def test_runtime_logs_command_reports_the_dispatched_workflow_run() -> None:
    workflow = _workflow()

    assert "listWorkflowRuns" in workflow
    assert "workflow_id: 'deploy-server.yml'" in workflow
    assert "run.head_sha === sourceSha" in workflow
    assert "Runtime logs workflow" in workflow


def test_runtime_logs_uses_same_pinned_host_key_fallback_as_canonical_release() -> None:
    release = _release_workflow()
    server = _server_workflow()
    fallback = (
        "secrets.VPS_SSH_HOST_KEY_SHA256 || "
        "'SHA256:cUfeWKyHoFIhKsV17Bpk076calxUaxd90XhuETb4vng'"
    )

    assert fallback in release
    assert server.count(fallback) >= 2

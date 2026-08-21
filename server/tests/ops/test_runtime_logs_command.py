from pathlib import Path


def _workflow() -> str:
    root = Path(__file__).resolve().parents[3]
    return (root / ".github/workflows/runtime-release-command.yml").read_text()


def test_owner_runtime_logs_command_dispatches_read_only_server_logs() -> None:
    workflow = _workflow()

    assert "'/runtime-logs'" in workflow
    assert "workflow_id: 'deploy-server.yml'" in workflow
    assert "action: 'logs'" in workflow
    assert "source_ref: sourceSha" in workflow

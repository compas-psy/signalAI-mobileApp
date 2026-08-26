from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_ROOT.parent
HELPER = SERVER_ROOT / "deploy" / "ensure_runtime_secrets.sh"
COMPOSE = SERVER_ROOT / "docker-compose.yml"
BOOTSTRAP = SERVER_ROOT / "deploy" / "bootstrap.sh"
DEPLOY_RELEASE = REPO_ROOT / ".github" / "workflows" / "deploy-release.yml"
DEPLOY_SERVER = REPO_ROOT / ".github" / "workflows" / "deploy-server.yml"
DEPLOY_SERVER_PACKAGE = REPO_ROOT / ".github" / "workflows" / "deploy-server-package.yml"


def _env_value(path: Path, name: str) -> str:
    prefix = f"{name}="
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    raise AssertionError(f"{name} not found")


def test_runtime_secret_helper_creates_private_stable_lighter_live_key_without_logging_it(
    tmp_path,
) -> None:
    assert HELPER.is_file(), "deploy/ensure_runtime_secrets.sh must provision the live vault key"
    env_file = tmp_path / ".env"
    env_file.write_text("POSTGRES_USER=signalai\n", encoding="utf-8")
    os.chmod(env_file, 0o600)

    first = subprocess.run(
        ["bash", str(HELPER), str(env_file)],
        check=True,
        capture_output=True,
        text=True,
    )
    key = _env_value(env_file, "SIGNALAI_LIGHTER_LIVE_SECRETS_KEY")
    assert len(key) >= 32
    assert "\n" not in key and "\r" not in key
    assert key not in first.stdout
    assert key not in first.stderr
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

    second = subprocess.run(
        ["bash", str(HELPER), str(env_file)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert _env_value(env_file, "SIGNALAI_LIGHTER_LIVE_SECRETS_KEY") == key
    assert key not in second.stdout
    assert key not in second.stderr


def test_runtime_secret_helper_persists_exact_deployment_source_from_server_env(
    tmp_path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("POSTGRES_USER=signalai\n", encoding="utf-8")
    os.chmod(env_file, 0o600)
    source_sha = "a" * 40
    env = dict(os.environ, SIGNALAI_SOURCE_SHA=source_sha)

    subprocess.run(
        ["bash", str(HELPER), str(env_file)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert _env_value(env_file, "SIGNALAI_SOURCE_SHA") == source_sha
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_only_api_and_execution_receive_lighter_live_vault_key() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    binding = "SIGNALAI_LIGHTER_LIVE_SECRETS_KEY: ${SIGNALAI_LIGHTER_LIVE_SECRETS_KEY:-}"
    assert compose.count(binding) == 2


def test_every_production_server_deploy_path_ensures_dedicated_live_vault_key() -> None:
    helper_name = "ensure_runtime_secrets.sh"
    assert helper_name in BOOTSTRAP.read_text(encoding="utf-8")
    assert helper_name in DEPLOY_RELEASE.read_text(encoding="utf-8")
    assert helper_name in DEPLOY_SERVER.read_text(encoding="utf-8")
    assert helper_name in DEPLOY_SERVER_PACKAGE.read_text(encoding="utf-8")

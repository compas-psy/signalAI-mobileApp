from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "ensure_runtime_secrets.sh"


def _run(env_file: Path, attestation: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(env_file), str(attestation)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_runtime_env_replaces_stale_source_sha_and_preserves_vault_key(tmp_path):
    env_file = tmp_path / ".env"
    vault_key = "e" * 64
    env_file.write_text(
        "POSTGRES_USER=signalai\n"
        f"SIGNALAI_LIGHTER_LIVE_SECRETS_KEY={vault_key}\n"
        f"SIGNALAI_SOURCE_SHA={'a' * 40}\n",
        encoding="utf-8",
    )
    attestation = tmp_path / ".signalai-source-sha"
    attestation.write_text("b" * 40 + "\n", encoding="utf-8")

    completed = _run(env_file, attestation)

    assert completed.returncode == 0, completed.stderr
    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert lines.count(f"SIGNALAI_SOURCE_SHA={'b' * 40}") == 1
    assert not any(line == f"SIGNALAI_SOURCE_SHA={'a' * 40}" for line in lines)
    assert f"SIGNALAI_LIGHTER_LIVE_SECRETS_KEY={vault_key}" in lines
    assert stat.S_IMODE(os.stat(env_file).st_mode) == 0o600


def test_runtime_env_rejects_invalid_release_attestation_without_replacing_sha(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"SIGNALAI_LIGHTER_LIVE_SECRETS_KEY={'f' * 64}\n"
        f"SIGNALAI_SOURCE_SHA={'c' * 40}\n",
        encoding="utf-8",
    )
    attestation = tmp_path / ".signalai-source-sha"
    attestation.write_text("not-a-commit\n", encoding="utf-8")

    completed = _run(env_file, attestation)

    assert completed.returncode != 0
    assert "attestation" in completed.stderr
    assert f"SIGNALAI_SOURCE_SHA={'c' * 40}" in env_file.read_text(encoding="utf-8")

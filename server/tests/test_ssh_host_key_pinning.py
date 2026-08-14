"""Production SSH must verify an approved host-key fingerprint before use."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PREPARE = ROOT / ".github" / "scripts" / "prepare_known_host.sh"
SSH_WORKFLOWS = (
    ROOT / ".github" / "workflows" / "deploy-release.yml",
    ROOT / ".github" / "workflows" / "deploy-server.yml",
    ROOT / ".github" / "workflows" / "deploy-server-package.yml",
    ROOT / ".github" / "workflows" / "sync-telegram-secrets.yml",
)


def _fake_tool(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _run_prepare(tmp_path: Path, fingerprint: str) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_tool(
        bin_dir,
        "ssh-keyscan",
        "printf '%s\\n' 'prod.example ssh-ed25519 AAAATESTKEY'",
    )
    _fake_tool(
        bin_dir,
        "ssh-keygen",
        "cat >/dev/null; printf '%s\\n' '256 SHA256:approved-pin prod.example (ED25519)'",
    )
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return subprocess.run(
        [str(PREPARE), "prod.example", fingerprint],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_matching_scanned_key_is_installed_only_after_fingerprint_verification(tmp_path: Path):
    result = _run_prepare(tmp_path, "SHA256:approved-pin")

    assert result.returncode == 0, result.stderr
    known_hosts = tmp_path / "home" / ".ssh" / "known_hosts"
    assert known_hosts.read_text(encoding="utf-8") == (
        "prod.example ssh-ed25519 AAAATESTKEY\n"
    )


def test_mismatched_fingerprint_fails_closed_without_known_host(tmp_path: Path):
    result = _run_prepare(tmp_path, "SHA256:different-pin")

    assert result.returncode != 0
    assert "host key fingerprint mismatch" in result.stderr.lower()
    known_hosts = tmp_path / "home" / ".ssh" / "known_hosts"
    assert not known_hosts.exists() or known_hosts.read_text(encoding="utf-8") == ""


def test_missing_or_malformed_expected_fingerprint_fails_before_trust(tmp_path: Path):
    for fingerprint in ("", "approved-pin", "MD5:001122"):
        result = _run_prepare(tmp_path / fingerprint.replace(":", "_"), fingerprint)
        assert result.returncode != 0


def test_all_production_ssh_workflows_use_shared_pin_and_no_tofu_fallback():
    for workflow in SSH_WORKFLOWS:
        text = workflow.read_text(encoding="utf-8")
        assert "VPS_SSH_HOST_KEY_SHA256" in text, workflow.name
        assert ".github/scripts/prepare_known_host.sh" in text, workflow.name
        assert "StrictHostKeyChecking=accept-new" not in text, workflow.name
        assert "ssh-keyscan" not in text, workflow.name


def test_pin_helper_compares_sha256_before_writing_known_hosts():
    text = PREPARE.read_text(encoding="utf-8")
    assert "ssh-keyscan" in text
    assert "ssh-keygen" in text
    assert "-E sha256" in text
    assert "VPS_SSH_HOST_KEY_SHA256" in text
    assert "known_hosts" in text

#!/usr/bin/env python3
"""Static invariants for SignalAI delivery workflows.

This is not a replacement for GitHub's workflow parser. It protects the
project-specific properties that generic YAML validation cannot express:
immutable source provenance, manual-only VPS mutation and stable signing.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def read(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def require(text: str, needle: str, context: str) -> None:
    if needle not in text:
        raise AssertionError(f"{context}: missing {needle!r}")


def forbid(text: str, needle: str, context: str) -> None:
    if needle in text:
        raise AssertionError(f"{context}: forbidden {needle!r}")


def main() -> int:
    quality = read("quality.yml")
    sideload = read("android-sideload.yml")
    deploy_release = read("deploy-release.yml")
    deploy_server = read("deploy-server.yml")
    release_command = read("runtime-release-command.yml")
    build_script = (ROOT / "tool" / "build_apk.sh").read_text(encoding="utf-8")
    bootstrap = (ROOT / "server" / "deploy" / "bootstrap.sh").read_text(
        encoding="utf-8"
    )

    for needle in (
        "workflow_call:",
        "image: postgres:16-alpine",
        "uses: astral-sh/setup-uv@v6",
        "version: '0.11.33'",
        "cache-dependency-glob: server/uv.lock",
        "uv lock --check",
        "uv sync --frozen --extra test",
        "git diff --exit-code -- uv.lock",
        ".venv/bin/alembic check",
        ".venv/bin/python -m pytest",
        "flutter analyze",
        "flutter test",
        "tool/ci_secret_scan.py",
    ):
        require(quality, needle, "quality.yml")
    forbid(quality, "pip install -e", "quality.yml")

    for needle in (
        "source_ref:",
        "needs.resolve.outputs.source_sha",
        "uses: ./.github/workflows/quality.yml",
        "group: signalai-sideload-publish",
        "SIDELOAD_KEYSTORE_BASE64",
        "signalai-sideload.apk",
        "signalai-sideload.json",
        '"bearer_token_compiled": False',
        '"source_sha_is_authoritative": True',
        "default: thin",
        "          - thin",
        "          - demo",
        "inputs.mode || 'thin'",
        '--dart-define=SIGNALAI_MODE="$MODE"',
        '"local_scanner_enabled": False',
        '"paper_lifecycle_owner": "server"',
        "Personal builds allow only thin or demo",
        "legacy `local`",
    ):
        require(sideload, needle, "android-sideload.yml")
    for needle in (
        "actions/cache",
        "--dart-define=SIGNALAI_DEVICE_TOKEN",
        "keytool -genkey",
        "git tag -f",
        "git push --force",
        "default: local",
        "          - local",
        "inputs.mode || 'local'",
        "SIGNALAI_MODE=local",
    ):
        forbid(sideload, needle, "android-sideload.yml")

    # Chat/connector-accessible runtime commands are deliberately narrow. Only
    # the repository owner, on the dedicated runtime issue, may dispatch an APK
    # build or the canonical cumulative release, or inspect recent APK runs.
    # Delivery commands resolve the immutable current default SHA first.
    for needle in (
        "issue_comment:",
        "github.event.issue.number == 1",
        "github.actor == github.repository_owner",
        "github.event.comment.body == '/build-apk'",
        "github.event.comment.body == '/release-full'",
        "github.event.comment.body == '/apk-status'",
        "actions: write",
        "actions.listWorkflowRuns",
        "workflow_id: 'android-sideload.yml'",
        "repos.getBranch",
        "branch.data.commit.sha",
        "workflow_id: 'release-cumulative.yml'",
        "source_ref: sourceSha",
    ):
        require(release_command, needle, "runtime-release-command.yml")
    for needle in (
        "pull_request:",
        "push:",
        "schedule:",
    ):
        forbid(release_command, needle, "runtime-release-command.yml")

    for needle in (
        'BUILD_MODE="${SIGNALAI_MODE:-thin}"',
        "thin|demo)",
        "flutter pub get --enforce-lockfile",
        '--dart-define=SIGNALAI_MODE="$BUILD_MODE"',
        "допустимы только thin и demo",
    ):
        require(build_script, needle, "tool/build_apk.sh")
    for needle in (
        'SIGNALAI_MODE:-local',
        "local|demo)",
    ):
        forbid(build_script, needle, "tool/build_apk.sh")

    for needle in (
        "SIGNALAI_DEVICE_TOKEN_FILE",
        "server runtime only",
        "Android Keystore",
        'Authorization: Bearer \\$SIGNALAI_TOKEN',
        "--dart-define=SIGNALAI_MODE=thin",
    ):
        require(bootstrap, needle, "server/deploy/bootstrap.sh")
    forbid(
        bootstrap,
        "--dart-define=SIGNALAI_DEVICE_TOKEN",
        "server/deploy/bootstrap.sh",
    )
    forbid(
        bootstrap,
        'DEVICE_TOKEN="$(head -c',
        "server/deploy/bootstrap.sh",
    )

    # Shared production-deploy safety properties. The release workflow and the
    # legacy server workflow differ only in how the exact source reaches VPS.
    for name, workflow in (
        ("deploy-release.yml", deploy_release),
        ("deploy-server.yml", deploy_server),
    ):
        require(workflow, "workflow_dispatch:", name)
        require(workflow, "source_ref:", name)
        require(workflow, "needs.resolve.outputs.source_sha", name)
        require(workflow, "group: signalai-production-deploy", name)
        require(workflow, "secrets.SIGNALAI_DEVICE_TOKEN", name)
        require(workflow, '[ -n "$DEVICE_TOKEN" ]', name)
        require(workflow, "must be one line of URL-safe characters", name)
        require(workflow, '"${#DEVICE_TOKEN}" -ge 32', name)
        require(workflow, "SIGNALAI_DEVICE_TOKEN=%s", name)
        require(workflow, "trap cleanup_token EXIT", name)
        forbid(workflow, "\n  pull_request:", name)
        forbid(workflow, "\n  push:", name)
        forbid(workflow, "claude/release-", name)
        forbid(workflow, "claude/ux-", name)
        forbid(workflow, "BRANCH=", name)
        forbid(workflow, "openssl dgst", name)
        forbid(workflow, "signalai-device-token-v1", name)
        forbid(workflow, "EXPLICIT_TOKEN", name)
        forbid(workflow, "set -euxo", name)

    # Canonical production deploy does not grant a production host access to
    # the private GitHub repository. Actions checks out the QA-verified SHA,
    # archives it and sends the archive over the already-authenticated SSH
    # channel. VPS verifies the archive digest before staging it.
    for needle in (
        "git archive --format=tar.gz",
        "/tmp/signalai-source.tar.gz",
        "source_bundle_sha256",
        "sha256sum -c signalai-source.tar.gz.sha256",
        'STAGE="$HOME/.signalai-stage-$SOURCE_SHA"',
        'printf \'%s\\n\' "$SOURCE_SHA" > "$STAGE/.signalai-source-sha"',
    ):
        require(deploy_release, needle, "deploy-release.yml")
    forbid(
        deploy_release,
        'git fetch --depth 1 origin "$SOURCE_SHA"',
        "deploy-release.yml",
    )

    # The older deploy-server path is retained for compatibility, but it must
    # still pin the requested commit rather than deploying a floating branch.
    require(
        deploy_server,
        'git fetch --depth 1 origin "$SOURCE_SHA"',
        "deploy-server.yml",
    )

    print("Delivery workflow invariants passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

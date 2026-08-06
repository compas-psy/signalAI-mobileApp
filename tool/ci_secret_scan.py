#!/usr/bin/env python3
"""Fail CI on high-confidence secrets committed to the repository.

The scanner intentionally uses only Python and git so it is available on a
clean GitHub runner.  It is deliberately conservative: false positives make
people bypass security checks, so only private-key material, well-known token
formats and forbidden secret files are rejected.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


MAX_TEXT_FILE_BYTES = 3 * 1024 * 1024

FORBIDDEN_FILENAMES = {
    ".env",
    "key.properties",
}
FORBIDDEN_SUFFIXES = {
    ".base64",
    ".jks",
    ".key",
    ".keystore",
    ".p12",
    ".pem",
    ".pfx",
}
ALLOWED_FILENAMES = {
    ".env.example",
}


@dataclass(frozen=True)
class Signature:
    name: str
    pattern: re.Pattern[str]


SIGNATURES = (
    Signature(
        "private key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
    Signature("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    Signature(
        "GitHub fine-grained token",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    ),
    Signature("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    Signature("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    Signature(
        "Slack token",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    ),
    Signature("Stripe live key", re.compile(r"\bsk_live_[A-Za-z0-9]{20,}\b")),
    Signature(
        "OpenAI API key",
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b"),
    ),
    Signature(
        "Anthropic API key",
        re.compile(r"\bsk-ant-[A-Za-z0-9_-]{32,}\b"),
    ),
    Signature(
        "Telegram bot token",
        re.compile(r"\b[0-9]{8,12}:[A-Za-z0-9_-]{30,}\b"),
    ),
)


def tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [root / item.decode() for item in result.stdout.split(b"\0") if item]


def annotation(path: Path, line: int, message: str) -> None:
    relative = path.as_posix()
    print(f"::error file={relative},line={line}::{message}")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings = 0

    for path in tracked_files(root):
        relative = path.relative_to(root)
        name = relative.name
        suffix = relative.suffix.lower()

        if name not in ALLOWED_FILENAMES and (
            name in FORBIDDEN_FILENAMES or suffix in FORBIDDEN_SUFFIXES
        ):
            annotation(relative, 1, "secret-bearing file must not be tracked")
            findings += 1
            continue

        try:
            if path.stat().st_size > MAX_TEXT_FILE_BYTES:
                continue
            raw = path.read_bytes()
        except (FileNotFoundError, OSError):
            continue
        if b"\0" in raw:
            continue

        text = raw.decode("utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for signature in SIGNATURES:
                if signature.pattern.search(line):
                    annotation(relative, line_number, f"possible {signature.name}")
                    findings += 1

        # A Dart define is compiled into the APK and can be extracted in
        # minutes.  The first release uses manual runtime token entry; the
        # mobile client then stores it in Keystore. Pairing is a later API.
        if relative.as_posix() in {
            ".github/workflows/android-release.yml",
            ".github/workflows/android-sideload.yml",
        } and "--dart-define=SIGNALAI_DEVICE_TOKEN" in text:
            line_number = text[: text.index("--dart-define=SIGNALAI_DEVICE_TOKEN")].count(
                "\n"
            ) + 1
            annotation(
                relative,
                line_number,
                "device bearer token must not be compiled into a mobile artifact",
            )
            findings += 1

    if findings:
        print(f"Secret scan failed: {findings} finding(s).", file=sys.stderr)
        return 1
    print("Secret scan passed: no tracked secret material found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

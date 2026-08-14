#!/usr/bin/env python3
"""Validate that a cumulative release targets the immutable default-branch head."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def validate_source_ref(source_ref: str, default_head_sha: str) -> str:
    """Return the accepted SHA or raise ValueError before any delivery work."""
    if not _SHA_RE.fullmatch(source_ref):
        raise ValueError("source_ref must be a lowercase 40-character commit SHA")
    if not _SHA_RE.fullmatch(default_head_sha):
        raise ValueError("repository default head is not a valid lowercase commit SHA")
    if source_ref != default_head_sha:
        raise ValueError(
            "source_ref must equal the current immutable default-branch head "
            f"({default_head_sha})"
        )
    return source_ref


def _github_json(url: str, token: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def resolve_default_head(*, repository: str, token: str, api_url: str) -> str:
    repo_url = f"{api_url.rstrip('/')}/repos/{repository}"
    repo = _github_json(repo_url, token)
    default_branch = repo.get("default_branch")
    if not isinstance(default_branch, str) or not default_branch:
        raise RuntimeError("GitHub did not return a default branch")

    encoded_branch = urllib.parse.quote(default_branch, safe="")
    ref = _github_json(f"{repo_url}/git/ref/heads/{encoded_branch}", token)
    sha = ref.get("object", {}).get("sha")
    if not isinstance(sha, str):
        raise RuntimeError("GitHub did not return the default-branch head SHA")
    return sha


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--github-token", required=True)
    parser.add_argument(
        "--api-url",
        default=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
    )
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    args = parser.parse_args()

    try:
        default_head = resolve_default_head(
            repository=args.repository,
            token=args.github_token,
            api_url=args.api_url,
        )
        accepted = validate_source_ref(args.source_ref, default_head)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"release source rejected: {exc}", file=sys.stderr)
        return 2

    if not args.github_output:
        print(accepted)
        return 0
    with open(args.github_output, "a", encoding="utf-8") as output:
        output.write(f"source_sha={accepted}\n")
    print(f"release source accepted: {accepted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

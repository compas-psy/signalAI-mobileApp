from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "validate_release_source.py"
WORKFLOW = ROOT / ".github" / "workflows" / "release-cumulative.yml"

spec = importlib.util.spec_from_file_location("validate_release_source", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
validate_source_ref = module.validate_source_ref

HEAD = "6b98506b6c13041373ee26f148d980fe264fe8da"


def test_accepts_exact_current_default_head() -> None:
    assert validate_source_ref(HEAD, HEAD) == HEAD


@pytest.mark.parametrize(
    "source_ref",
    [
        "claude/release-y40hk5",
        "v1.2.3",
        "6b98506",
        HEAD.upper(),
        "z" * 40,
    ],
)
def test_rejects_branch_tag_malformed_or_non_lowercase_sha(source_ref: str) -> None:
    with pytest.raises(ValueError):
        validate_source_ref(source_ref, HEAD)


def test_rejects_valid_historical_sha() -> None:
    historical = "375d5fecd3b301676720430a11090b51834a10b7"
    with pytest.raises(ValueError, match="current immutable default-branch head"):
        validate_source_ref(historical, HEAD)


def test_workflow_routes_only_validated_output_downstream() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    quality_and_dispatch = workflow.split("  quality:\n", 1)[1]

    assert "needs.validate.outputs.source_sha" in quality_and_dispatch
    assert "source_ref: ${{ needs.validate.outputs.source_sha }}" in quality_and_dispatch
    assert "SOURCE_REF: ${{ needs.validate.outputs.source_sha }}" in quality_and_dispatch
    assert "inputs.source_ref" not in quality_and_dispatch

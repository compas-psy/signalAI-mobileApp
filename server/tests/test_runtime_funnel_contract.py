from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "runtime_funnel_48h.py"
WORKFLOW = ROOT / ".github" / "workflows" / "runtime-funnel.yml"


def _integer_constant(source: str, name: str) -> int:
    match = re.search(rf"^{name}\s*=\s*(\d+)\s*$", source, re.MULTILINE)
    assert match is not None, f"{name} must be an explicit funnel contract constant"
    return int(match.group(1))


def test_funnel_loads_enough_daily_and_hourly_history_independently() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert _integer_constant(source, "D1_LOOKBACK_DAYS") >= 100
    assert _integer_constant(source, "H1_LOOKBACK_DAYS") >= 35
    assert "d1_history_floor" in source
    assert "h1_history_floor" in source
    assert "Bar.timeframe == Timeframe.D1" in source
    assert "Bar.timeframe == Timeframe.H1" in source


def test_funnel_workflow_allows_full_canonical_forts_runtime() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(r"timeout-minutes:\s*(\d+)", source)
    assert match is not None
    assert int(match.group(1)) >= 40

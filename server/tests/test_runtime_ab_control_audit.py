from pathlib import Path


def _script() -> str:
    return (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "scripts"
        / "runtime_ab_control_audit.py"
    ).read_text(encoding="utf-8")


def test_runtime_ab_audit_does_not_reference_nonexistent_bar_id():
    script = _script()

    assert "select(Bar.id)" not in script
    assert "select(Bar.open_time)" in script


def test_runtime_ab_audit_reports_comparable_48h_control_window_and_daily_emissions():
    script = _script()

    assert "start_48 = now - timedelta(hours=48)" in script
    assert "AB_AUDIT_PAPER_AB_48H" in script
    assert "AB_AUDIT_PAPER_AB_48H_STAT" in script
    assert "AB_AUDIT_PAPER_AB_SIGNAL_DAY" in script

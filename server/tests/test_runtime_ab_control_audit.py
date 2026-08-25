from pathlib import Path


def test_runtime_ab_audit_does_not_reference_nonexistent_bar_id():
    script = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "scripts"
        / "runtime_ab_control_audit.py"
    ).read_text(encoding="utf-8")

    assert "select(Bar.id)" not in script
    assert "select(Bar.open_time)" in script

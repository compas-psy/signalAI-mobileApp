from app.investment_radar import SNAPSHOT_FLAG, SNAPSHOT_SOURCE


def test_daily_radar_snapshot_has_dedicated_operational_identity():
    assert SNAPSHOT_SOURCE == "equity_radar"
    assert SNAPSHOT_FLAG == "DAILY_SNAPSHOT"

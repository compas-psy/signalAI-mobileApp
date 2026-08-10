from app.main import app


def test_equity_radar_route_is_registered():
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/v1/investment-radar" in paths

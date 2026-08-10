from app.main import app


def test_equity_radar_route_is_registered():
    paths = {route.path for route in app.routes}
    assert "/api/v1/investment-radar" in paths

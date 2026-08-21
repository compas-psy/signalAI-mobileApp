import pytest

from app.execution.venues.base import CORE_EXECUTION_CAPABILITIES, validate_adapter
from app.execution.venues.capabilities import VenueCapability, require_capabilities


def test_lighter_profile_is_core_compatible_without_special_casing_execution_core() -> None:
    from app.execution.venues.lighter import LIGHTER_VENUE_PROFILE

    assert LIGHTER_VENUE_PROFILE.venue == "LIGHTER"
    assert validate_adapter(LIGHTER_VENUE_PROFILE) is LIGHTER_VENUE_PROFILE
    check = require_capabilities(
        LIGHTER_VENUE_PROFILE.capabilities,
        CORE_EXECUTION_CAPABILITIES,
    )
    assert check.allowed is True
    assert check.missing == ()


def test_lighter_capabilities_are_explicit_and_documented_not_assumed() -> None:
    from app.execution.venues.lighter import LIGHTER_CAPABILITIES

    documented = {
        VenueCapability.MARKET_ORDER,
        VenueCapability.LIMIT_ORDER,
        VenueCapability.POST_ONLY,
        VenueCapability.REDUCE_ONLY,
        VenueCapability.STOP_PROTECTION,
        VenueCapability.CANCEL_REPLACE,
        VenueCapability.CLIENT_ORDER_ID,
        VenueCapability.WEBSOCKET_ACCOUNT_EVENTS,
        VenueCapability.FUNDING,
        VenueCapability.MIN_QTY_STEP,
        VenueCapability.SANDBOX,
        VenueCapability.RECONCILIATION_QUERY,
    }
    for capability in documented:
        assert LIGHTER_CAPABILITIES.supports(capability), capability

    # SAI-066 must not turn an unverified provider detail into a capability.
    # Account/margin/leverage facts are a later R5 slice.
    assert not LIGHTER_CAPABILITIES.supports(VenueCapability.LEVERAGE_TIERS)


def test_lighter_environment_selection_is_explicit_and_fail_closed() -> None:
    from app.execution.venues.lighter import (
        LighterEnvironment,
        lighter_base_url,
        lighter_websocket_url,
    )

    assert lighter_base_url(LighterEnvironment.MAINNET) == (
        "https://mainnet.zklighter.elliot.ai"
    )
    assert lighter_base_url(LighterEnvironment.TESTNET) == (
        "https://testnet.zklighter.elliot.ai"
    )
    assert lighter_websocket_url(LighterEnvironment.MAINNET) == (
        "wss://mainnet.zklighter.elliot.ai/stream"
    )
    assert lighter_websocket_url(LighterEnvironment.TESTNET) == (
        "wss://testnet.zklighter.elliot.ai/stream"
    )

    with pytest.raises(ValueError, match="unknown Lighter environment"):
        lighter_base_url("production")


def test_lighter_environment_metadata_contains_no_credentials_or_live_toggle() -> None:
    from app.execution.venues.lighter import LIGHTER_VENUE_PROFILE

    rendered = repr(LIGHTER_VENUE_PROFILE).lower()
    for forbidden in (
        "private_key",
        "api_secret",
        "eth_private",
        "live_enabled",
        "activate_live",
    ):
        assert forbidden not in rendered


def test_lighter_profile_is_contract_metadata_not_an_execution_adapter() -> None:
    from app.execution.venues.base import VenueAdapter
    from app.execution.venues.lighter import LIGHTER_VENUE_PROFILE

    assert not isinstance(LIGHTER_VENUE_PROFILE, VenueAdapter)
    for provider_action in (
        "submit",
        "consume_fills",
        "arm_protection",
        "emergency_flatten",
    ):
        assert not hasattr(LIGHTER_VENUE_PROFILE, provider_action)

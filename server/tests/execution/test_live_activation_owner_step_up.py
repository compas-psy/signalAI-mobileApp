from __future__ import annotations

import pytest


def test_live_confirmation_rejects_client_boolean_without_cryptographic_step_up(session) -> None:
    """A stolen device bearer plus owner_confirmed=true is never owner authority."""

    from app.execution.live_activation import (
        LiveActivationRejected,
        confirm_live_activation,
    )

    with pytest.raises(LiveActivationRejected, match="step-up"):
        confirm_live_activation(
            session,
            preview_hash="attacker-controlled-preview",
            idempotency_key="attacker-controlled-replay-key",
            owner_confirmed=True,
        )

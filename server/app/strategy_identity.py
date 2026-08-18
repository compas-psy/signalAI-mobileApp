"""Stable strategy identity constants shared by persistence and measurement.

These values describe provenance only.  Nothing in this module controls
whether a strategy scans, emits signals, notifies, or advances through the
paper lifecycle.
"""

LEGACY_CONTROL_VERSION = "legacy_control_v1"
LEGACY_CONTROL_SOURCE_SHA = "74de570dcaf90900ece5c8e8c6c5f558ca4f49d7"
LEGACY_CONTROL_CONFIG_HASH = (
    "110d5b5d29560e762f2ee15528bd03ed6ae30b0e6a652b94a40b40eeabd51ada"
)
LEGACY_RISK_POLICY_VERSION = "legacy_risk_policy@74de570dcaf9"
LEGACY_CONTROL_ROLE = "CONTROL"
LEGACY_CONTROL_GENERATED_STAGE = "PAPER"
LEGACY_UNKNOWN = "legacy_unknown"

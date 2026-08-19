"""Provider-neutral execution venue interfaces."""

from .base import (
    CORE_EXECUTION_CAPABILITIES,
    VenueAdapter,
    VenueAdapterConfigurationError,
    validate_adapter,
)
from .capabilities import (
    VenueCapabilities,
    VenueCapability,
    VenueCapabilityCheck,
    require_capabilities,
)

__all__ = [
    "CORE_EXECUTION_CAPABILITIES",
    "VenueAdapter",
    "VenueAdapterConfigurationError",
    "VenueCapabilities",
    "VenueCapability",
    "VenueCapabilityCheck",
    "require_capabilities",
    "validate_adapter",
]

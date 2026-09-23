"""Read-only job discovery and verified target identity."""

from .core import (
    DiscoveryCandidate,
    DiscoveryRequest,
    DiscoveryResult,
    DiscoverySnapshot,
    VerifiedJobTarget,
    evaluate_snapshot,
)

__all__ = [
    "DiscoveryCandidate", "DiscoveryRequest", "DiscoveryResult",
    "DiscoverySnapshot", "VerifiedJobTarget", "evaluate_snapshot",
]

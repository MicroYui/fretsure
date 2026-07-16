"""Optional FastAPI adapter for Fretsure's application service."""

from fretsure.api.app import (
    API_VERSION,
    MAX_API_CANDIDATES,
    MAX_API_REPAIR_ITERS,
    create_app,
)

__all__ = [
    "API_VERSION",
    "MAX_API_CANDIDATES",
    "MAX_API_REPAIR_ITERS",
    "create_app",
]

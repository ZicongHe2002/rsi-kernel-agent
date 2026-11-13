"""RSI experiment foundation; no execution backend is loaded by this package."""

from .contracts import SCHEMA_VERSION, ValidationError

__all__ = ["SCHEMA_VERSION", "ValidationError"]

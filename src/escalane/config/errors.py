"""Custom exception hierarchy for Escalane."""

from __future__ import annotations

from typing import Any


class EscalaneError(Exception):
    """Base error with a stable public message and optional structured details."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Serialize the error without exposing implementation details."""
        result: dict[str, Any] = {"error": self.message}
        if self.details:
            result["details"] = self.details
        return result


class ConfigurationError(EscalaneError):
    """Raised when deployment configuration is missing or invalid."""


class ValidationError(EscalaneError):
    """Raised when client-provided data is invalid."""

    def __init__(
        self,
        message: str,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details)
        self.field = field

    def to_dict(self) -> dict[str, Any]:
        """Convert exception to dictionary for API responses."""
        result = super().to_dict()
        if self.field:
            result["field"] = self.field
        return result


class NotFoundError(EscalaneError):
    """Raised when a requested resource is not found."""

    def __init__(
        self,
        resource_type: str,
        resource_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        message = f"{resource_type} not found"
        if resource_id:
            message = f"{resource_type} '{resource_id}' not found"
        super().__init__(message, details)
        self.resource_type = resource_type
        self.resource_id = resource_id


class ConflictError(EscalaneError):
    """Raised when an operation conflicts with current state."""


class ConnectorError(EscalaneError):
    """Raised when an external provider request fails."""

    def __init__(
        self,
        connector: str,
        operation: str,
        original_error: Exception | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        message = f"{connector} error during {operation}"
        if original_error:
            message = f"{message}: {original_error}"
        super().__init__(message, details)
        self.connector = connector
        self.operation = operation
        self.original_error = original_error


class RateLimitError(EscalaneError):
    """Raised when rate limit is exceeded."""

    def __init__(
        self,
        limit: int,
        window_seconds: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        message = f"Rate limit exceeded: {limit} requests per {window_seconds} seconds"
        super().__init__(message, details)
        self.limit = limit
        self.window_seconds = window_seconds


class AuthenticationError(EscalaneError):
    """Raised when authentication fails."""


class AuthorizationError(EscalaneError):
    """Raised when an authenticated actor lacks permission."""

"""Custom exception hierarchy for Escalane."""

from __future__ import annotations

from typing import Any


class EscalaneError(Exception):
    """Base exception for all Escalane errors.

    All custom exceptions should inherit from this class for
    consistent error handling and logging.
    """

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        """Initialize the exception.

        Args:
            message: Human-readable error message
            details: Optional additional error details
        """
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Convert exception to dictionary for API responses.

        Returns:
            Dictionary representation of the error
        """
        result: dict[str, Any] = {"error": self.message}
        if self.details:
            result["details"] = self.details
        return result


class ConfigurationError(EscalaneError):
    """Raised when configuration is missing or invalid.

    This error indicates a setup/deployment issue that requires
    administrator intervention.
    """

    pass


class ValidationError(EscalaneError):
    """Raised when input validation fails.

    This error indicates client-provided data is invalid.
    """

    def __init__(
        self,
        message: str,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the validation error.

        Args:
            message: Human-readable error message
            field: Optional field name that failed validation
            details: Optional additional error details
        """
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
        """Initialize the not found error.

        Args:
            resource_type: Type of resource (e.g., "alarm", "device")
            resource_id: Optional ID of the resource
            details: Optional additional error details
        """
        message = f"{resource_type} not found"
        if resource_id:
            message = f"{resource_type} '{resource_id}' not found"
        super().__init__(message, details)
        self.resource_type = resource_type
        self.resource_id = resource_id


class ConflictError(EscalaneError):
    """Raised when an operation conflicts with current state.

    Examples:
        - Invalid alarm state transition
        - Duplicate resource creation
    """

    pass


class ConnectorError(EscalaneError):
    """Raised when an external service request fails.

    This error wraps failures from external integrations like
    Zammad, SMS providers, or Signal.
    """

    def __init__(
        self,
        connector: str,
        operation: str,
        original_error: Exception | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the connector error.

        Args:
            connector: Name of the connector (e.g., "zammad", "signal")
            operation: Operation that failed (e.g., "create_ticket")
            original_error: Original exception that caused the failure
            details: Optional additional error details
        """
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
        """Initialize the rate limit error.

        Args:
            limit: Maximum requests allowed
            window_seconds: Time window in seconds
            details: Optional additional error details
        """
        message = f"Rate limit exceeded: {limit} requests per {window_seconds} seconds"
        super().__init__(message, details)
        self.limit = limit
        self.window_seconds = window_seconds


class AuthenticationError(EscalaneError):
    """Raised when authentication fails."""

    pass


class AuthorizationError(EscalaneError):
    """Raised when authorization fails.

    This error indicates the user is authenticated but lacks
    permission for the requested operation.
    """

    pass


class IdempotencyError(EscalaneError):
    """Raised when idempotency check fails."""

    pass

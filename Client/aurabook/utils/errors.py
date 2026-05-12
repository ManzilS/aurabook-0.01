"""Structured error types used across the AuraBook local client."""

from __future__ import annotations

from typing import Any


class AuraBookError(Exception):
    """Base class for all structured AuraBook errors.

    Args:
        message: Human-readable error message.
        adapter: Adapter name when the error originates from an upstream adapter.
        details: Optional detailed diagnostics for development mode.
    """

    status_code: int = 500
    error_type: str = "internal_error"

    def __init__(self, message: str, *, adapter: str = "", details: str = "") -> None:
        self.message = message
        self.adapter = adapter
        self.details = details
        super().__init__(message)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        """Convert the error into an OpenAI-style JSON payload."""
        payload: dict[str, dict[str, Any]] = {
            "error": {
                "type": self.error_type,
                "message": self.message,
            }
        }
        if self.adapter:
            payload["error"]["adapter"] = self.adapter
        if self.details:
            payload["error"]["details"] = self.details
        return payload


class AdapterError(AuraBookError):
    """An upstream adapter failed."""

    status_code = 502
    error_type = "adapter_error"


class AdapterTimeoutError(AdapterError):
    """The upstream adapter timed out."""

    status_code = 504
    error_type = "adapter_timeout"


class AdapterAuthError(AdapterError):
    """The upstream adapter rejected authentication."""

    status_code = 401
    error_type = "adapter_auth_error"


class AdapterRateLimitError(AdapterError):
    """The upstream adapter rate-limited the request."""

    status_code = 429
    error_type = "adapter_rate_limited"


class AdapterNotFoundError(AuraBookError):
    """The requested adapter does not exist."""

    status_code = 400
    error_type = "adapter_not_found"


class RequestValidationError(AuraBookError):
    """The inbound request is invalid."""

    status_code = 400
    error_type = "validation_error"


class RequestTooLargeError(AuraBookError):
    """The inbound request body is too large."""

    status_code = 413
    error_type = "request_too_large"


class AuthenticationError(AuraBookError):
    """The client is not authorized to use the local proxy."""

    status_code = 401
    error_type = "authentication_error"


class CloudBrainError(Exception):
    """Raised when the Cloud Brain request path fails."""

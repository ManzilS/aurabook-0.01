"""Tests for structured error classes."""

from aurabook.utils.errors import (
    AdapterAuthError,
    AdapterError,
    AdapterNotFoundError,
    AdapterRateLimitError,
    AdapterTimeoutError,
    AuraBookError,
    AuthenticationError,
    RequestTooLargeError,
    RequestValidationError,
)


def test_error_payload_includes_optional_fields() -> None:
    error = AuraBookError("boom", adapter="openai_proxy", details="traceback")

    payload = error.to_dict()

    assert payload["error"]["type"] == "internal_error"
    assert payload["error"]["adapter"] == "openai_proxy"
    assert payload["error"]["details"] == "traceback"


def test_error_hierarchy() -> None:
    assert issubclass(AdapterError, AuraBookError)
    assert issubclass(AdapterTimeoutError, AdapterError)
    assert issubclass(AdapterAuthError, AdapterError)
    assert issubclass(AdapterRateLimitError, AdapterError)
    assert issubclass(AdapterNotFoundError, AuraBookError)
    assert issubclass(RequestValidationError, AuraBookError)
    assert issubclass(RequestTooLargeError, AuraBookError)
    assert issubclass(AuthenticationError, AuraBookError)

from __future__ import annotations

from typing import Any


class ToolError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def __str__(self) -> str:
        return self.message

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": "error",
            "code": self.code,
            "error": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class ApiRequestError(ToolError):
    def __init__(
        self,
        *,
        endpoint: str,
        status_code: int | None,
        message: str | None = None,
    ):
        self.endpoint = endpoint
        self.status_code = status_code
        details: dict[str, Any] = {"endpoint": endpoint}
        if status_code is not None:
            details["status_code"] = status_code
        rendered = message or (
            f"ZenMoney API request failed: {endpoint}"
            if status_code is None
            else f"ZenMoney API request failed with HTTP {status_code}: {endpoint}"
        )
        super().__init__("API_REQUEST_FAILED", rendered, details)


class AuthenticationError(ToolError):
    def __init__(self, *, endpoint: str, status_code: int = 401):
        self.endpoint = endpoint
        self.status_code = status_code
        super().__init__(
            "AUTHENTICATION_FAILED",
            "ZenMoney API rejected the access token (401). "
            "Get a new token from https://budgera.com/settings/export",
            {"endpoint": endpoint, "status_code": status_code},
        )


class InvalidArgumentError(ToolError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__("INVALID_ARGUMENT", message, details)


class InvalidBoolError(ToolError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__("INVALID_BOOL", message, details)


class InvalidUUIDError(ToolError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__("INVALID_UUID", message, details)


class InvalidDateError(ToolError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__("INVALID_DATE", message, details)


class InvalidMonthError(ToolError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__("INVALID_MONTH", message, details)


class InvalidDateRangeError(ToolError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__("INVALID_DATE_RANGE", message, details)


class AmbiguousCategoryError(ToolError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__("AMBIGUOUS_CATEGORY", message, details)


class UnsupportedCategoryFilterError(ToolError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__("UNSUPPORTED_CATEGORY_FILTER", message, details)


class InvalidCategoryError(ToolError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__("INVALID_CATEGORY", message, details)


class EntityNotFoundError(ToolError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__("ENTITY_NOT_FOUND", message, details)


class UnsupportedCalculationError(ToolError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__("UNSUPPORTED_CALCULATION", message, details)

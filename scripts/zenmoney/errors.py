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

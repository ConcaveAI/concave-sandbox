"""
Exception classes for the Concave sandbox SDK.

This module defines all custom exceptions that can be raised during sandbox operations.
"""

from typing import Optional


class SandboxError(Exception):
    """Base exception for all sandbox operations."""

    pass


# Client Errors (4xx - user's fault)
class SandboxClientError(SandboxError):
    """Base exception for client-side errors (4xx HTTP status codes)."""

    pass


class SandboxAuthenticationError(SandboxClientError):
    """Raised when API authentication fails (401, 403)."""

    pass


class SandboxNotFoundError(SandboxClientError):
    """Raised when trying to operate on a non-existent sandbox (404)."""

    pass


class SandboxRateLimitError(SandboxClientError):
    """
    Raised when hitting rate limits or concurrency limits (429).

    Attributes:
        message: Error message from the server
        limit: Maximum allowed (if available)
        current: Current count (if available)
    """

    def __init__(self, message: str, limit: Optional[int] = None, current: Optional[int] = None):
        super().__init__(message)
        self.limit = limit
        self.current = current


class SandboxValidationError(SandboxClientError):
    """Raised when input validation fails (invalid parameters, empty code, etc.)."""

    pass


# Server Errors (5xx - server's fault)
class SandboxServerError(SandboxError):
    """Base exception for server-side errors (5xx HTTP status codes)."""

    def __init__(self, message: str, status_code: Optional[int] = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class SandboxUnavailableError(SandboxServerError):
    """Raised when sandbox service is unavailable (502, 503)."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message, status_code, retryable=True)


class SandboxInternalError(SandboxServerError):
    """Raised when sandbox service has internal errors (500)."""

    def __init__(self, message: str):
        super().__init__(message, status_code=500, retryable=False)


# Network Errors
class SandboxNetworkError(SandboxError):
    """Base exception for network-related errors."""

    pass


class SandboxConnectionError(SandboxNetworkError):
    """Raised when unable to connect to the sandbox service."""

    pass


class SandboxTimeoutError(SandboxNetworkError):
    """
    Raised when a request or operation times out.

    Attributes:
        timeout_ms: Timeout duration in milliseconds
        operation: The operation that timed out
    """

    def __init__(
        self, message: str, timeout_ms: Optional[int] = None, operation: Optional[str] = None
    ):
        super().__init__(message)
        self.timeout_ms = timeout_ms
        self.operation = operation


# Execution and Creation Errors (kept for backwards compatibility)
class SandboxCreationError(SandboxError):
    """Raised when sandbox creation fails."""

    pass


class SandboxExecutionError(SandboxError):
    """Raised when command or code execution fails."""

    pass


# Response Errors
class SandboxInvalidResponseError(SandboxError):
    """Raised when API returns unexpected or malformed response."""

    pass


# File Operation Errors
class SandboxFileError(SandboxError):
    """Base exception for file operation failures."""

    pass


class SandboxFileExistsError(SandboxFileError):
    def __init__(self, message: str, path: str):
        super().__init__(message)
        self.path = path


class SandboxFileLockedError(SandboxFileError):
    def __init__(self, message: str, path: str):
        super().__init__(message)
        self.path = path


class SandboxFileBusyError(SandboxFileError):
    def __init__(self, message: str, path: str):
        super().__init__(message)
        self.path = path


class SandboxInsufficientStorageError(SandboxFileError):
    def __init__(self, message: str, path: str):
        super().__init__(message)
        self.path = path


class SandboxPermissionDeniedError(SandboxFileError):
    def __init__(self, message: str, path: str):
        super().__init__(message)
        self.path = path


class SandboxUnsupportedMediaTypeError(SandboxFileError):
    def __init__(self, message: str, detail: str | None = None):
        super().__init__(message)
        self.detail = detail


class SandboxChecksumMismatchError(SandboxFileError):
    def __init__(self, message: str, path: str, expected: str, actual: str, algorithm: str, direction: str):
        super().__init__(message)
        self.path = path
        self.expected = expected
        self.actual = actual
        self.algorithm = algorithm
        self.direction = direction


class SandboxFileNotFoundError(SandboxFileError):
    """
    Raised when a file is not found (local or remote).

    Attributes:
        path: Path to the file that was not found
        is_local: True if local file, False if remote file
    """

    def __init__(self, message: str, path: str, is_local: bool = True):
        super().__init__(message)
        self.path = path
        self.is_local = is_local


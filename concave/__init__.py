"""
Concave SDK - Python client for the Concave sandbox service.

This module provides a simple, intuitive interface for creating and managing
isolated code execution environments backed by Firecracker sandboxes. The SDK handles
sandbox lifecycle management, command execution, and Python and JavaScript code sandboxing.

Example usage (traditional way):
    from concave import Sandbox

    # Create a new sandbox
    sbx = Sandbox.create()

    # Execute shell commands
    output = sbx.execute("uname -a")
    print(output.stdout)

    # Run Python code
    result = sbx.run("print(1337)")
    print(result.stdout)

    # Clean up
    sbx.delete()

Example usage (context manager way):
    from concave import sandbox

    # Automatically create and clean up sandbox
    with sandbox() as s:
        result = s.run("print('Hello from Concave!')")
        print(result.stdout)
    # Sandbox is automatically deleted after the with block
"""

__version__ = "0.7.2"

from .sandbox import Sandbox, sandbox
from .results import ExecuteResult, RunResult, SandboxList
from .namespaces import FilesNamespace, NetworkNamespace, MonitorNamespace
from .exceptions import (
    # Base exceptions
    SandboxError,
    SandboxClientError,
    SandboxServerError,
    SandboxNetworkError,
    # Client errors (4xx)
    SandboxAuthenticationError,
    SandboxNotFoundError,
    SandboxRateLimitError,
    SandboxValidationError,
    # Server errors (5xx)
    SandboxUnavailableError,
    SandboxInternalError,
    # Network errors
    SandboxConnectionError,
    SandboxTimeoutError,
    # File operation errors
    SandboxFileError,
    SandboxFileExistsError,
    SandboxFileNotFoundError,
    SandboxFileLockedError,
    SandboxFileBusyError,
    SandboxInsufficientStorageError,
    SandboxPermissionDeniedError,
    SandboxUnsupportedMediaTypeError,
    SandboxChecksumMismatchError,
    # Legacy errors (kept for backwards compatibility)
    SandboxCreationError,
    SandboxExecutionError,
    # Response errors
    SandboxInvalidResponseError,
)

__all__ = [
    # Core classes
    "Sandbox",
    "sandbox",
    # Result classes
    "ExecuteResult",
    "RunResult",
    "SandboxList",
    # Namespace classes
    "FilesNamespace",
    "NetworkNamespace",
    "MonitorNamespace",
    # Base exceptions
    "SandboxError",
    "SandboxClientError",
    "SandboxServerError",
    "SandboxNetworkError",
    # Client errors
    "SandboxAuthenticationError",
    "SandboxNotFoundError",
    "SandboxRateLimitError",
    "SandboxValidationError",
    # Server errors
    "SandboxUnavailableError",
    "SandboxInternalError",
    # Network errors
    "SandboxConnectionError",
    "SandboxTimeoutError",
    # File operation errors
    "SandboxFileError",
    "SandboxFileExistsError",
    "SandboxFileNotFoundError",
    "SandboxFileLockedError",
    "SandboxFileBusyError",
    "SandboxInsufficientStorageError",
    "SandboxPermissionDeniedError",
    "SandboxUnsupportedMediaTypeError",
    "SandboxChecksumMismatchError",
    # Legacy errors
    "SandboxCreationError",
    "SandboxExecutionError",
    # Response errors
    "SandboxInvalidResponseError",
]

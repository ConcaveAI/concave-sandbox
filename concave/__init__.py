"""
Concave SDK - Python client for the Concave sandbox service.

This module provides a simple, intuitive interface for creating and managing
isolated code execution environments backed by Firecracker sandboxes. The SDK handles
sandbox lifecycle management, command execution, and Python code sandboxing.

Example usage (traditional way):
    from concave import Sandbox

    # Create a new sandbox
    sbx = Sandbox.create(name="test")

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
    with sandbox(name="test") as s:
        result = s.run("print('Hello from Concave!')")
        print(result.stdout)
    # Sandbox is automatically deleted after the with block
"""

__version__ = "0.2.1"

from .sandbox import (
    ExecuteResult,
    RunResult,
    Sandbox,
    # Client errors (4xx)
    SandboxAuthenticationError,
    SandboxClientError,
    # Network errors
    SandboxConnectionError,
    # Legacy errors (kept for backwards compatibility)
    SandboxCreationError,
    # Base exceptions
    SandboxError,
    SandboxExecutionError,
    # File operation errors
    SandboxFileError,
    SandboxFileNotFoundError,
    SandboxFileSizeError,
    SandboxInternalError,
    # Response errors
    SandboxInvalidResponseError,
    SandboxNetworkError,
    SandboxNotFoundError,
    SandboxRateLimitError,
    SandboxServerError,
    SandboxTimeoutError,
    # Server errors (5xx)
    SandboxUnavailableError,
    SandboxValidationError,
    sandbox,
)

__all__ = [
    "Sandbox",
    "ExecuteResult",
    "RunResult",
    "sandbox",
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
    "SandboxFileNotFoundError",
    "SandboxFileSizeError",
    # Legacy errors
    "SandboxCreationError",
    "SandboxExecutionError",
    # Response errors
    "SandboxInvalidResponseError",
]

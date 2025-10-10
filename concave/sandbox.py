"""
Sandbox client implementation for the Concave service.

This module provides the core Sandbox class that manages sandbox lifecycle and
code execution through the Concave sandbox API. It handles HTTP communication,
error management, and provides a clean interface for sandbox operations.
"""

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx


@dataclass
class ExecuteResult:
    """
    Result from executing a shell command in the sandbox.

    Attributes:
        stdout: Standard output from the command
        stderr: Standard error from the command
        returncode: Exit code from the command (0 = success)
        command: The original command that was executed
    """

    stdout: str
    stderr: str
    returncode: int
    command: str


@dataclass
class RunResult:
    """
    Result from running Python code in the sandbox.

    Attributes:
        stdout: Standard output from the Python execution
        stderr: Standard error from the Python execution
        returncode: Exit code from the Python execution (0 = success)
        code: The original Python code that was executed
    """

    stdout: str
    stderr: str
    returncode: int
    code: str


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


class Sandbox:
    """
    Main interface for interacting with the Concave sandbox service.

    This class manages the lifecycle of isolated code execution environments,
    providing methods to create, execute commands, run Python code, and clean up
    sandbox instances. Each sandbox is backed by a Firecracker VM for strong
    isolation while maintaining fast performance.

    The sandbox automatically handles HTTP communication with the service,
    error handling, and response parsing to provide a clean Python interface.
    """

    def __init__(self, sandbox_id: str, name: str, base_url: str, api_key: Optional[str] = None):
        """
        Initialize a Sandbox instance.

        Args:
            sandbox_id: Unique identifier for the sandbox (UUID)
            name: Human-readable name for the sandbox
            base_url: Base URL of the sandbox service
            api_key: API key for authentication

        Note:
            This constructor should not be called directly. Use Sandbox.create() instead.
        """
        self.sandbox_id = sandbox_id
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.created_at = time.time()
        self.api_key = api_key

        # Pre-compute API route roots
        self.api_base = f"{self.base_url}/api/v1"
        self._sandboxes_url = f"{self.api_base}/sandboxes"

        # HTTP client configuration
        headers = {"User-Agent": "concave-sandbox/0.1.2", "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        self._client = httpx.Client(timeout=httpx.Timeout(30.0), headers=headers)

    @classmethod
    def create(
        cls, name: str, base_url: Optional[str] = None, api_key: Optional[str] = None
    ) -> "Sandbox":
        """
        Create a new sandbox instance.

        Args:
            name: Human-readable name for the sandbox
            base_url: Base URL of the sandbox service (defaults to CONCAVE_SANDBOX_BASE_URL env var or https://api.concave.dev)
            api_key: API key for authentication (defaults to CONCAVE_SANDBOX_API_KEY env var)

        Returns:
            A new Sandbox instance ready for code execution

        Raises:
            SandboxCreationError: If sandbox creation fails
            ValueError: If api_key is not provided and CONCAVE_SANDBOX_API_KEY is not set

        Example:
            sbx = Sandbox.create(name="my-test-sandbox", api_key="cnc_abc123...")
        """
        if base_url is None:
            base_url = os.getenv("CONCAVE_SANDBOX_BASE_URL", "https://api.concave.dev")

        if api_key is None:
            api_key = os.getenv("CONCAVE_SANDBOX_API_KEY")
            if not api_key:
                raise ValueError(
                    "api_key must be provided or CONCAVE_SANDBOX_API_KEY environment variable must be set"
                )

        # Create HTTP client for the creation request
        headers = {
            "User-Agent": "concave-sandbox/0.1.2",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        client = httpx.Client(timeout=httpx.Timeout(30.0), headers=headers)

        try:
            # Make creation request to the sandbox service
            base = base_url.rstrip("/")
            response = client.post(f"{base}/api/v1/sandboxes")
            response.raise_for_status()
            sandbox_data = response.json()

            # Validate response contains required fields
            if "id" not in sandbox_data:
                raise SandboxInvalidResponseError(
                    f"Invalid response from sandbox service: {sandbox_data}"
                )

            sandbox_id = sandbox_data["id"]
            return cls(sandbox_id, name, base_url, api_key)

        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            error_msg = f"HTTP {status_code}"
            try:
                error_data = e.response.json()
                if "error" in error_data:
                    error_msg += f": {error_data['error']}"
            except Exception:
                error_msg += f": {e.response.text}"

            # Raise specific exceptions based on status code
            if status_code == 401 or status_code == 403:
                raise SandboxAuthenticationError(f"Authentication failed: {error_msg}") from e
            elif status_code == 429:
                raise SandboxRateLimitError(f"Rate limit exceeded: {error_msg}") from e
            elif status_code == 500:
                raise SandboxInternalError(f"Server error: {error_msg}") from e
            elif status_code == 502 or status_code == 503:
                raise SandboxUnavailableError(
                    f"Service unavailable: {error_msg}", status_code
                ) from e
            else:
                raise SandboxCreationError(f"Failed to create sandbox: {error_msg}") from e

        except httpx.TimeoutException as e:
            raise SandboxTimeoutError(
                "Sandbox creation timed out", timeout_ms=30000, operation="create"
            ) from e
        except httpx.RequestError as e:
            raise SandboxConnectionError(f"Failed to connect to sandbox service: {e}") from e
        finally:
            client.close()

    def execute(self, command: str, timeout: Optional[int] = None) -> ExecuteResult:
        """
        Execute a shell command in the sandbox.

        Args:
            command: Shell command to execute (e.g., "python -V", "ls -la")
            timeout: Timeout in milliseconds (default: 10000ms)

        Returns:
            ExecuteResult containing stdout, stderr, return code, and original command

        Raises:
            SandboxExecutionError: If the execution request fails
            SandboxNotFoundError: If the sandbox is not found
            ValueError: If command is empty

        Example:
            result = sbx.execute("sleep 2", timeout=5000)  # 5 second timeout
            print(f"Output: {result.stdout}")
            print(f"Exit code: {result.returncode}")
        """
        if not command.strip():
            raise SandboxValidationError("Command cannot be empty")

        # Prepare request payload
        payload = {"command": command}
        if timeout is not None:
            payload["timeout"] = timeout

        # Set per-request timeout (ms to seconds + buffer)
        request_timeout = 12.0  # default: 10s + 2s buffer
        if timeout is not None and timeout > 0:
            request_timeout = (timeout / 1000.0) + 2.0

        try:
            response = self._client.post(
                f"{self._sandboxes_url}/{self.sandbox_id}/exec",
                json=payload,
                timeout=request_timeout,
            )
            response.raise_for_status()
            data = response.json()

            # Handle error responses from the service
            if "error" in data:
                if "sandbox not found" in data["error"].lower():
                    raise SandboxNotFoundError(f"Sandbox {self.sandbox_id} not found")
                raise SandboxExecutionError(f"Execution failed: {data['error']}")

            return ExecuteResult(
                stdout=data.get("stdout", ""),
                stderr=data.get("stderr", ""),
                returncode=data.get("returncode", -1),
                command=command,
            )

        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            if status_code == 404:
                raise SandboxNotFoundError(f"Sandbox {self.sandbox_id} not found") from e
            elif status_code == 401 or status_code == 403:
                raise SandboxAuthenticationError("Authentication failed") from e
            elif status_code == 429:
                raise SandboxRateLimitError("Rate limit exceeded") from e
            elif status_code == 500:
                raise SandboxInternalError("Server error during command execution") from e
            elif status_code == 502 or status_code == 503:
                raise SandboxUnavailableError(
                    f"Sandbox {self.sandbox_id} is not ready or unreachable", status_code
                ) from e
            else:
                error_msg = f"HTTP {status_code}"
                try:
                    error_data = e.response.json()
                    if "error" in error_data:
                        error_msg += f": {error_data['error']}"
                except Exception:
                    error_msg += f": {e.response.text}"
                raise SandboxExecutionError(f"Failed to execute command: {error_msg}") from e

        except httpx.TimeoutException as e:
            timeout_val = timeout if timeout else 10000
            raise SandboxTimeoutError(
                "Command execution timed out", timeout_ms=timeout_val, operation="execute"
            ) from e
        except httpx.RequestError as e:
            raise SandboxConnectionError(f"Failed to connect to sandbox service: {e}") from e

    def run(self, code: str, timeout: Optional[int] = None) -> RunResult:
        """
        Run Python code in the sandbox with tmpfs-backed isolation.

        Args:
            code: Python code to execute
            timeout: Timeout in milliseconds (default: 10000ms)

        Returns:
            RunResult containing stdout, stderr, return code, and original code

        Raises:
            SandboxExecutionError: If the execution request fails
            SandboxNotFoundError: If the sandbox is not found
            ValueError: If code is empty

        Example:
            result = sbx.run("import time; time.sleep(1)", timeout=3000)  # 3 second timeout
            print(result.stdout)
        """
        if not code.strip():
            raise SandboxValidationError("Code cannot be empty")

        # Prepare request payload
        request_data = {"code": code}
        if timeout is not None:
            request_data["timeout"] = timeout

        # Set per-request timeout (ms to seconds + buffer)
        request_timeout = 12.0  # default: 10s + 2s buffer
        if timeout is not None and timeout > 0:
            request_timeout = (timeout / 1000.0) + 2.0

        try:
            response = self._client.post(
                f"{self._sandboxes_url}/{self.sandbox_id}/python",
                json=request_data,
                timeout=request_timeout,
            )
            response.raise_for_status()
            data = response.json()

            # Handle error responses from the service
            if "error" in data:
                if "sandbox not found" in data["error"].lower():
                    raise SandboxNotFoundError(f"Sandbox {self.sandbox_id} not found")
                raise SandboxExecutionError(f"Python execution failed: {data['error']}")

            return RunResult(
                stdout=data.get("stdout", ""),
                stderr=data.get("stderr", ""),
                returncode=data.get("returncode", -1),
                code=code,
            )

        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            if status_code == 404:
                raise SandboxNotFoundError(f"Sandbox {self.sandbox_id} not found") from e
            elif status_code == 401 or status_code == 403:
                raise SandboxAuthenticationError("Authentication failed") from e
            elif status_code == 429:
                raise SandboxRateLimitError("Rate limit exceeded") from e
            elif status_code == 500:
                raise SandboxInternalError("Server error during Python execution") from e
            elif status_code == 502 or status_code == 503:
                raise SandboxUnavailableError(
                    f"Sandbox {self.sandbox_id} is not ready or unreachable", status_code
                ) from e
            else:
                error_msg = f"HTTP {status_code}"
                try:
                    error_data = e.response.json()
                    if "error" in error_data:
                        error_msg += f": {error_data['error']}"
                except Exception:
                    error_msg += f": {e.response.text}"
                raise SandboxExecutionError(f"Failed to run Python code: {error_msg}") from e

        except httpx.TimeoutException as e:
            timeout_val = timeout if timeout else 10000
            raise SandboxTimeoutError(
                "Python execution timed out", timeout_ms=timeout_val, operation="run"
            ) from e
        except httpx.RequestError as e:
            raise SandboxConnectionError(f"Failed to connect to sandbox service: {e}") from e

    def delete(self) -> bool:
        """
        Delete the sandbox and free up resources.

        Returns:
            True if deletion was successful, False otherwise

        Example:
            success = sbx.delete()
            if success:
                print("Sandbox deleted successfully")

        Note:
            After calling delete(), this Sandbox instance should not be used
            for further operations as the underlying sandbox will be destroyed.
        """
        try:
            response = self._client.delete(f"{self._sandboxes_url}/{self.sandbox_id}")
            response.raise_for_status()
            data = response.json()

            # Check if deletion was successful
            return data.get("status") == "deleted"

        except (httpx.HTTPStatusError, httpx.RequestError):
            # Log the error but don't raise - deletion might have already occurred
            return False

    def status(self) -> Dict[str, Any]:
        """
        Get the current status of the sandbox.

        Returns:
            Dictionary containing sandbox status information including:
            - id: Sandbox identifier
            - ip: Sandbox IP address
            - pid: Sandbox process ID
            - state: Current sandbox state
            - started_at: Sandbox start timestamp
            - exec_count: Number of commands executed
            - guest_health: Health status of the guest sandbox runtime

        Raises:
            SandboxNotFoundError: If the sandbox is not found
            SandboxExecutionError: If status check fails

        Example:
            status = sbx.status()
            print(f"Sandbox State: {status['state']}")
            print(f"Commands executed: {status['exec_count']}")
        """
        try:
            response = self._client.get(f"{self._sandboxes_url}/{self.sandbox_id}")
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            if status_code == 404:
                raise SandboxNotFoundError(f"Sandbox {self.sandbox_id} not found") from e
            elif status_code == 401 or status_code == 403:
                raise SandboxAuthenticationError("Authentication failed") from e
            elif status_code == 500:
                raise SandboxInternalError("Server error while fetching status") from e
            elif status_code == 502 or status_code == 503:
                raise SandboxUnavailableError("Service unavailable", status_code) from e
            else:
                error_msg = f"HTTP {status_code}"
                try:
                    error_data = e.response.json()
                    if "error" in error_data:
                        error_msg += f": {error_data['error']}"
                except Exception:
                    error_msg += f": {e.response.text}"
                raise SandboxExecutionError(f"Failed to get status: {error_msg}") from e

        except httpx.TimeoutException as e:
            raise SandboxTimeoutError(
                "Status check timed out", timeout_ms=5000, operation="status"
            ) from e
        except httpx.RequestError as e:
            raise SandboxConnectionError(f"Failed to connect to sandbox service: {e}") from e

    def __enter__(self):
        """Context manager entry - returns self for use in with statements."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - automatically deletes sandbox on exit."""
        self.delete()
        self._client.close()

    def __repr__(self):
        """String representation of the Sandbox instance."""
        return f"Sandbox(id={self.sandbox_id}, name='{self.name}', created_at={self.created_at})"


@contextmanager
def sandbox(name: str = "sandbox", base_url: Optional[str] = None, api_key: Optional[str] = None):
    """
    Context manager for creating and automatically cleaning up a sandbox.

    This provides a cleaner way to work with sandboxes by automatically
    handling creation and deletion using Python's with statement.

    Args:
        name: Human-readable name for the sandbox (default: "sandbox")
        base_url: Base URL of the sandbox service (defaults to CONCAVE_SANDBOX_BASE_URL env var or https://api.concave.dev)
        api_key: API key for authentication (defaults to CONCAVE_SANDBOX_API_KEY env var)

    Yields:
        Sandbox: A sandbox instance ready for code execution

    Raises:
        SandboxCreationError: If sandbox creation fails
        ValueError: If api_key is not provided and CONCAVE_SANDBOX_API_KEY env var is not set

    Example:
        ```python
        from concave import sandbox

        with sandbox(name="my-test") as s:
            result = s.run("print('Hello from Concave!')")
            print(result.stdout)
        # Sandbox is automatically deleted after the with block
        ```
    """
    sbx = Sandbox.create(name=name, base_url=base_url, api_key=api_key)
    try:
        yield sbx
    finally:
        sbx.delete()
        sbx._client.close()

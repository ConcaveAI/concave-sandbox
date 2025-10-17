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

from . import __version__


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
    Result from running code in the sandbox.

    Attributes:
        stdout: Standard output from the code execution
        stderr: Standard error from the code execution
        returncode: Exit code from the code execution (0 = success)
        code: The original code that was executed
        language: The language that was executed (currently only python)
    """

    stdout: str
    stderr: str
    returncode: int
    code: str
    language: str = "python"


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


class SandboxFileSizeError(SandboxFileError):
    """
    Raised when file exceeds size limits.

    Attributes:
        max_size: Maximum allowed file size in bytes
        actual_size: Actual file size in bytes
    """

    def __init__(self, message: str, max_size: int, actual_size: int):
        super().__init__(message)
        self.max_size = max_size
        self.actual_size = actual_size


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

    @staticmethod
    def _get_credentials(
        base_url: Optional[str] = None, api_key: Optional[str] = None
    ) -> tuple[str, str]:
        """
        Get base_url and api_key from arguments or environment variables.

        Args:
            base_url: Optional base URL
            api_key: Optional API key

        Returns:
            Tuple of (base_url, api_key)

        Raises:
            ValueError: If api_key is not provided and CONCAVE_SANDBOX_API_KEY is not set
        """
        if base_url is None:
            base_url = os.getenv("CONCAVE_SANDBOX_BASE_URL", "https://api.concave.dev")

        if api_key is None:
            api_key = os.getenv("CONCAVE_SANDBOX_API_KEY")
            if not api_key:
                raise ValueError(
                    "api_key must be provided or CONCAVE_SANDBOX_API_KEY environment variable must be set"
                )

        return base_url, api_key

    @staticmethod
    def _create_http_client(api_key: str, timeout: float = 30.0) -> httpx.Client:
        """
        Create an HTTP client with proper headers.

        Args:
            api_key: API key for authentication
            timeout: Request timeout in seconds

        Returns:
            Configured httpx.Client
        """
        headers = {
            "User-Agent": f"concave-sandbox/{__version__}",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        return httpx.Client(timeout=httpx.Timeout(timeout), headers=headers)

    @staticmethod
    def _handle_http_error(e: httpx.HTTPStatusError, operation: str = "operation") -> None:
        """
        Handle HTTP status errors and raise appropriate exceptions.

        Args:
            e: The HTTP status error
            operation: Description of the operation that failed

        Raises:
            Appropriate SandboxError subclass based on status code
        """
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
        elif status_code == 404:
            raise SandboxNotFoundError(f"Not found: {error_msg}") from e
        elif status_code == 429:
            raise SandboxRateLimitError(f"Rate limit exceeded: {error_msg}") from e
        elif status_code == 500:
            raise SandboxInternalError(f"Server error: {error_msg}") from e
        elif status_code == 502 or status_code == 503:
            raise SandboxUnavailableError(f"Service unavailable: {error_msg}", status_code) from e
        else:
            raise SandboxError(f"Failed to {operation}: {error_msg}") from e

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
        headers = {"User-Agent": f"concave-sandbox/{__version__}", "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        self._client = httpx.Client(timeout=httpx.Timeout(30.0), headers=headers)

    @classmethod
    def get(cls, sandbox_id: str) -> "Sandbox":
        """
        Get an existing sandbox by its ID.

        Use this when you have a sandbox ID stored elsewhere and want to reconnect to it.

        Args:
            sandbox_id: The UUID of an existing sandbox

        Returns:
            Sandbox instance connected to the existing sandbox

        Raises:
            SandboxNotFoundError: If the sandbox doesn't exist
            SandboxAuthenticationError: If authentication fails
            ValueError: If CONCAVE_SANDBOX_API_KEY environment variable is not set

        Example:
            # Store the ID somewhere
            sbx = Sandbox.create(name="my-sandbox")
            sandbox_id = sbx.sandbox_id
            # ... save sandbox_id to database ...

            # Later, reconnect using the ID
            sbx = Sandbox.get(sandbox_id)
            result = sbx.execute("echo 'still here!'")
            print(result.stdout)
        """
        # Get credentials
        base_url, api_key = cls._get_credentials(None, None)

        # Create HTTP client to verify the sandbox exists
        client = cls._create_http_client(api_key)

        try:
            # Verify sandbox exists by fetching its info
            base = base_url.rstrip("/")
            response = client.get(f"{base}/api/v1/sandboxes/{sandbox_id}")
            response.raise_for_status()
            sandbox_data = response.json()

            # Create and return Sandbox instance
            return cls(
                sandbox_id=sandbox_id,
                name=sandbox_id,  # Use ID as name since we don't store custom names
                base_url=base_url,
                api_key=api_key,
            )

        except httpx.HTTPStatusError as e:
            cls._handle_http_error(e, "get sandbox")
        except httpx.TimeoutException as e:
            raise SandboxTimeoutError(
                f"Request timed out while fetching sandbox {sandbox_id}", 
                timeout_ms=30000, 
                operation="get"
            ) from e
        except httpx.RequestError as e:
            raise SandboxConnectionError(f"Failed to connect to sandbox service: {e}") from e
        finally:
            client.close()

    @classmethod
    def create(
        cls, name: str, internet_access: bool = True
    ) -> "Sandbox":
        """
        Create a new sandbox instance.

        Args:
            name: Human-readable name for the sandbox
            internet_access: Enable internet access for the sandbox (default: True)

        Returns:
            A new Sandbox instance ready for code execution

        Raises:
            SandboxCreationError: If sandbox creation fails
            ValueError: If CONCAVE_SANDBOX_API_KEY environment variable is not set

        Example:
            sbx = Sandbox.create(name="my-test-sandbox")
            sbx_no_internet = Sandbox.create(name="isolated-sandbox", internet_access=False)
        """
        # Get credentials using helper method
        base_url, api_key = cls._get_credentials(None, None)

        # Create HTTP client using helper method
        client = cls._create_http_client(api_key)

        try:
            # Make creation request to the sandbox service
            base = base_url.rstrip("/")
            response = client.put(f"{base}/api/v1/sandboxes", json={"internet_access": internet_access})
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
            cls._handle_http_error(e, "create sandbox")

        except httpx.TimeoutException as e:
            raise SandboxTimeoutError(
                "Sandbox creation timed out", timeout_ms=30000, operation="create"
            ) from e
        except httpx.RequestError as e:
            raise SandboxConnectionError(f"Failed to connect to sandbox service: {e}") from e
        finally:
            client.close()

    @classmethod
    def list_page(
        cls,
        limit: int = 100,
        cursor: Optional[str] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        state: Optional[str] = None,
        internet_access: Optional[bool] = None,
        min_exec_count: Optional[int] = None,
        max_exec_count: Optional[int] = None,
    ) -> dict:
        """
        List sandboxes with pagination metadata (single page).

        Returns a dictionary with sandboxes and pagination info for manual cursor-based pagination.

        Args:
            limit: Maximum number of sandboxes to return (default: 100)
            cursor: Pagination cursor for fetching next page
            since: Unix timestamp (epoch seconds) - only return sandboxes created at or after this time
            until: Unix timestamp (epoch seconds) - only return sandboxes created before this time
            state: Filter by sandbox state ("running", "stopped", "error")
            internet_access: Filter by internet access (True/False)
            min_exec_count: Minimum number of executions
            max_exec_count: Maximum number of executions

        Returns:
            Dictionary with keys:
            - 'sandboxes': List of Sandbox instances
            - 'count': Number of sandboxes in this response
            - 'has_more': Boolean indicating if more pages exist
            - 'next_cursor': String cursor for next page (None if no more pages)

        Example:
            # Manual pagination
            page1 = Sandbox.list_page(limit=50)
            print(f"Page 1: {page1['count']} sandboxes")
            
            # Filter by state
            running = Sandbox.list_page(state="running")
            
            # Multiple filters
            active = Sandbox.list_page(state="running", internet_access=True, min_exec_count=5)
        """
        # Get credentials using helper method
        base_url, api_key = cls._get_credentials(None, None)

        # Create HTTP client using helper method
        client = cls._create_http_client(api_key)

        try:
            # Build query params
            params = {"limit": str(limit)}
            if cursor:
                params["cursor"] = cursor
            if since is not None:
                params["since"] = str(since)
            if until is not None:
                params["until"] = str(until)
            if state:
                params["state"] = state
            if internet_access is not None:
                params["internet_access"] = "true" if internet_access else "false"
            if min_exec_count is not None:
                params["min_exec_count"] = str(min_exec_count)
            if max_exec_count is not None:
                params["max_exec_count"] = str(max_exec_count)

            # Make request
            base = base_url.rstrip("/")
            response = client.get(f"{base}/api/v1/sandboxes", params=params)
            response.raise_for_status()
            data = response.json()

            # Parse response
            sandboxes_data = data.get("sandboxes") or []

            # Create Sandbox instances
            sandbox_instances = []
            for sandbox_dict in sandboxes_data:
                sandbox_id = sandbox_dict.get("id")
                if sandbox_id:
                    sandbox = cls(
                        sandbox_id=sandbox_id,
                        name=sandbox_id,
                        base_url=base_url,
                        api_key=api_key,
                    )
                    sandbox_instances.append(sandbox)

            # Return full pagination response
            return {
                'sandboxes': sandbox_instances,
                'count': data.get('count', len(sandbox_instances)),
                'has_more': data.get('has_more', False),
                'next_cursor': data.get('next_cursor'),
            }

        except httpx.HTTPStatusError as e:
            cls._handle_http_error(e, "list sandboxes")
        except httpx.TimeoutException as e:
            raise SandboxTimeoutError(
                "List sandboxes request timed out", timeout_ms=30000, operation="list"
            ) from e
        except httpx.RequestError as e:
            raise SandboxConnectionError(f"Failed to connect to sandbox service: {e}") from e
        finally:
            client.close()

    @classmethod
    def list(
        cls,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        state: Optional[str] = None,
        internet_access: Optional[bool] = None,
        min_exec_count: Optional[int] = None,
        max_exec_count: Optional[int] = None,
    ) -> list["Sandbox"]:
        """
        List all active sandboxes for the authenticated user.

        Returns sandboxes sorted by creation time (newest first).

        Args:
            limit: Maximum number of sandboxes to return. If None (default), auto-paginates to fetch all.
                   If provided, returns only the first page (up to limit items).
            cursor: Pagination cursor for fetching next page (used with limit)
            since: Unix timestamp (epoch seconds) - only return sandboxes created at or after this time
            until: Unix timestamp (epoch seconds) - only return sandboxes created before this time
            state: Filter by sandbox state ("running", "stopped", "error")
            internet_access: Filter by internet access (True/False)
            min_exec_count: Minimum number of executions
            max_exec_count: Maximum number of executions

        Returns:
            List of Sandbox instances representing active sandboxes, sorted by newest first

        Raises:
            SandboxAuthenticationError: If authentication fails
            ValueError: If CONCAVE_SANDBOX_API_KEY environment variable is not set

        Example:
            # List all sandboxes (auto-paginates)
            sandboxes = Sandbox.list()
            print(f"Found {len(sandboxes)} active sandboxes")

            # List only running sandboxes
            running = Sandbox.list(state="running")
            
            # List sandboxes with internet and at least 5 executions
            active = Sandbox.list(internet_access=True, min_exec_count=5)

            # List sandboxes with time filter (epoch seconds)
            import time
            one_hour_ago = int(time.time()) - 3600
            recent_sandboxes = Sandbox.list(since=one_hour_ago, state="running")
        """
        # Get credentials using helper method
        base_url, api_key = cls._get_credentials(None, None)

        # Create HTTP client using helper method
        client = cls._create_http_client(api_key)

        # Auto-pagination: if limit is None, fetch all pages
        if limit is None:
            all_sandboxes = []
            current_cursor = cursor
            
            while True:
                # Build query params
                params = {}
                if current_cursor:
                    params["cursor"] = current_cursor
                if since is not None:
                    params["since"] = str(since)
                if until is not None:
                    params["until"] = str(until)
                if state:
                    params["state"] = state
                if internet_access is not None:
                    params["internet_access"] = "true" if internet_access else "false"
                if min_exec_count is not None:
                    params["min_exec_count"] = str(min_exec_count)
                if max_exec_count is not None:
                    params["max_exec_count"] = str(max_exec_count)

                try:
                    # Make request
                    base = base_url.rstrip("/")
                    response = client.get(f"{base}/api/v1/sandboxes", params=params)
                    response.raise_for_status()
                    data = response.json()

                    # Parse response
                    sandboxes_data = data.get("sandboxes") or []
                    
                    # Create Sandbox instances
                    for sandbox_dict in sandboxes_data:
                        sandbox_id = sandbox_dict.get("id")
                        if sandbox_id:
                            sandbox = cls(
                                sandbox_id=sandbox_id,
                                name=sandbox_id,
                                base_url=base_url,
                                api_key=api_key,
                            )
                            all_sandboxes.append(sandbox)

                    # Check if there are more pages
                    has_more = data.get("has_more", False)
                    next_cursor = data.get("next_cursor")
                    
                    if not has_more or not next_cursor:
                        break
                    
                    current_cursor = next_cursor

                except httpx.HTTPStatusError as e:
                    cls._handle_http_error(e, "list sandboxes")
                except httpx.TimeoutException as e:
                    raise SandboxTimeoutError(f"Request timed out while listing sandboxes: {str(e)}") from e
                except httpx.RequestError as e:
                    raise SandboxConnectionError(f"Connection failed while listing sandboxes: {str(e)}") from e

            return all_sandboxes

        # Single page fetch when limit is provided
        try:
            # Build query params
            params = {"limit": str(limit)}
            if cursor:
                params["cursor"] = cursor
            if since is not None:
                params["since"] = str(since)
            if until is not None:
                params["until"] = str(until)
            if state:
                params["state"] = state
            if internet_access is not None:
                params["internet_access"] = "true" if internet_access else "false"
            if min_exec_count is not None:
                params["min_exec_count"] = str(min_exec_count)
            if max_exec_count is not None:
                params["max_exec_count"] = str(max_exec_count)

            # Make request
            base = base_url.rstrip("/")
            response = client.get(f"{base}/api/v1/sandboxes", params=params)
            response.raise_for_status()
            data = response.json()

            # Parse response
            sandboxes_data = data.get("sandboxes") or []

            # Create Sandbox instances
            sandbox_instances = []
            for sandbox_dict in sandboxes_data:
                sandbox_id = sandbox_dict.get("id")
                if sandbox_id:
                    sandbox = cls(
                        sandbox_id=sandbox_id,
                        name=sandbox_id,
                        base_url=base_url,
                        api_key=api_key,
                    )
                    sandbox_instances.append(sandbox)

            return sandbox_instances

        except httpx.HTTPStatusError as e:
            cls._handle_http_error(e, "list sandboxes")

        except httpx.TimeoutException as e:
            raise SandboxTimeoutError(
                "List sandboxes request timed out", timeout_ms=30000, operation="list"
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
            self._handle_http_error(e, "execute command")

        except httpx.TimeoutException as e:
            timeout_val = timeout if timeout else 10000
            raise SandboxTimeoutError(
                "Command execution timed out", timeout_ms=timeout_val, operation="execute"
            ) from e
        except httpx.RequestError as e:
            raise SandboxConnectionError(f"Failed to connect to sandbox service: {e}") from e

    def run(self, code: str, timeout: Optional[int] = None, language: str = "python") -> RunResult:
        """
        Run code in the sandbox with tmpfs-backed isolation.

        Args:
            code: Code to execute
            timeout: Timeout in milliseconds (default: 10000ms)
            language: Programming language to use (default: "python"). Currently only Python is supported.

        Returns:
            RunResult containing stdout, stderr, return code, original code, and language

        Raises:
            SandboxExecutionError: If the execution request fails
            SandboxNotFoundError: If the sandbox is not found
            SandboxValidationError: If code is empty or language is unsupported

        Example:
            # Run Python code
            result = sbx.run("print('Hello, World!')")
            print(result.stdout)  # Hello, World!
            
            # Run Python with timeout
            result = sbx.run("import time; time.sleep(1)", timeout=3000)
            print(result.stdout)
        """
        if not code.strip():
            raise SandboxValidationError("Code cannot be empty")

        if language != "python":
            raise SandboxValidationError(f"Unsupported language: {language}. Currently only 'python' is supported.")

        # Prepare request payload
        request_data = {"code": code, "language": language}
        if timeout is not None:
            request_data["timeout"] = timeout

        # Set per-request timeout (ms to seconds + buffer)
        request_timeout = 12.0  # default: 10s + 2s buffer
        if timeout is not None and timeout > 0:
            request_timeout = (timeout / 1000.0) + 2.0

        try:
            response = self._client.post(
                f"{self._sandboxes_url}/{self.sandbox_id}/run",
                json=request_data,
                timeout=request_timeout,
            )
            response.raise_for_status()
            data = response.json()

            # Handle error responses from the service
            if "error" in data:
                if "sandbox not found" in data["error"].lower():
                    raise SandboxNotFoundError(f"Sandbox {self.sandbox_id} not found")
                raise SandboxExecutionError(f"Code execution failed: {data['error']}")

            return RunResult(
                stdout=data.get("stdout", ""),
                stderr=data.get("stderr", ""),
                returncode=data.get("returncode", -1),
                code=code,
                language=language,
            )

        except httpx.HTTPStatusError as e:
            self._handle_http_error(e, "run code")

        except httpx.TimeoutException as e:
            timeout_val = timeout if timeout else 10000
            raise SandboxTimeoutError(
                f"Code execution timed out", timeout_ms=timeout_val, operation="run"
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

    def ping(self) -> bool:
        """
        Ping the sandbox to check if it is responsive.

        Returns:
            True if sandbox is responsive, False otherwise

        Raises:
            SandboxNotFoundError: If the sandbox is not found
            SandboxAuthenticationError: If authentication fails
            SandboxTimeoutError: If the ping request times out

        Example:
            if sbx.ping():
                print("Sandbox is alive!")
            else:
                print("Sandbox is not responding")
        """
        try:
            response = self._client.get(
                f"{self._sandboxes_url}/{self.sandbox_id}/ping",
                timeout=5.0,
            )
            response.raise_for_status()
            data = response.json()

            return data.get("status") == "ok"

        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            if status_code == 404:
                raise SandboxNotFoundError(f"Sandbox {self.sandbox_id} not found") from e
            elif status_code == 401 or status_code == 403:
                raise SandboxAuthenticationError("Authentication failed") from e
            elif status_code == 502 or status_code == 503:
                raise SandboxUnavailableError(
                    f"Sandbox {self.sandbox_id} is not ready or unreachable", status_code
                ) from e
            else:
                # For other errors, return False instead of raising
                return False

        except httpx.TimeoutException as e:
            raise SandboxTimeoutError("Ping timed out", timeout_ms=5000, operation="ping") from e
        except httpx.RequestError:
            # Network errors -> sandbox is not reachable
            return False

    def uptime(self) -> float:
        """
        Get the uptime of the sandbox in seconds.

        Returns:
            Sandbox uptime in seconds as a float

        Raises:
            SandboxNotFoundError: If the sandbox is not found
            SandboxAuthenticationError: If authentication fails
            SandboxUnavailableError: If the sandbox is unavailable
            SandboxTimeoutError: If the uptime request times out
            SandboxExecutionError: If the uptime request fails

        Example:
            uptime_seconds = sbx.uptime()
            print(f"Sandbox has been running for {uptime_seconds:.2f} seconds")
        """
        try:
            response = self._client.get(
                f"{self._sandboxes_url}/{self.sandbox_id}/uptime",
                timeout=5.0,
            )
            response.raise_for_status()
            data = response.json()

            if "uptime" not in data:
                raise SandboxInvalidResponseError(
                    f"Invalid uptime response: missing 'uptime' field"
                )

            return float(data["uptime"])

        except httpx.HTTPStatusError as e:
            self._handle_http_error(e, "get uptime")

        except httpx.TimeoutException as e:
            raise SandboxTimeoutError(
                "Uptime request timed out", timeout_ms=5000, operation="uptime"
            ) from e
        except httpx.RequestError as e:
            raise SandboxConnectionError(f"Failed to connect to sandbox service: {e}") from e
        except (ValueError, TypeError) as e:
            raise SandboxInvalidResponseError(f"Invalid uptime value in response: {e}") from e

    def status(self) -> Dict[str, Any]:
        """
        Get the current status of the sandbox.

        Returns:
            Dictionary containing sandbox status information including:
            - id: Sandbox identifier
            - user_id: User who owns the sandbox
            - ip: Sandbox IP address
            - state: Current sandbox state (running, stopped, error)
            - started_at: Sandbox start timestamp
            - exec_count: Number of commands executed
            - internet_access: Whether internet access is enabled

        Raises:
            SandboxNotFoundError: If the sandbox is not found
            SandboxExecutionError: If status check fails

        Example:
            status = sbx.status()
            print(f"Sandbox State: {status['state']}")
            print(f"Commands executed: {status['exec_count']}")
            print(f"IP address: {status['ip']}")
        """
        try:
            response = self._client.get(f"{self._sandboxes_url}/{self.sandbox_id}")
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            self._handle_http_error(e, "get status")

        except httpx.TimeoutException as e:
            raise SandboxTimeoutError(
                "Status check timed out", timeout_ms=5000, operation="status"
            ) from e
        except httpx.RequestError as e:
            raise SandboxConnectionError(f"Failed to connect to sandbox service: {e}") from e

    def upload_file(self, local_path: str, remote_path: str, overwrite: bool = False) -> bool:
        """
        Upload a file from local filesystem to the sandbox.

        Args:
            local_path: Path to the local file to upload
            remote_path: Absolute path in the sandbox where file should be stored (must start with /)
            overwrite: If False (default), returns False when remote file exists. If True, overwrites existing file.

        Returns:
            True if upload was successful, False if file exists and overwrite=False

        Raises:
            SandboxFileNotFoundError: If local file doesn't exist
            SandboxFileSizeError: If file exceeds 4MB limit
            SandboxValidationError: If remote_path is not absolute
            SandboxNotFoundError: If sandbox is not found
            SandboxTimeoutError: If upload times out
            SandboxFileError: If upload fails for other reasons

        Example:
            # Upload a Python script (won't overwrite if exists)
            sbx.upload_file("./script.py", "/tmp/script.py")
            
            # Upload and overwrite if exists
            sbx.upload_file("./data.json", "/home/user/data.json", overwrite=True)

        Note:
            TODO: Temporary 4MB limit. Future versions will support streaming for larger files.
        """
        import base64

        # Validate local file exists
        if not os.path.exists(local_path):
            raise SandboxFileNotFoundError(
                f"Local file not found: {local_path}", path=local_path, is_local=True
            )

        # Validate remote path is absolute
        if not remote_path.startswith("/"):
            raise SandboxValidationError("Remote path must be absolute (start with /)")

        # Check file size
        # TODO: Temporary 4MB limit. Future versions will support streaming for larger files.
        file_size = os.path.getsize(local_path)
        max_size = 4 * 1024 * 1024  # 4MB
        if file_size > max_size:
            raise SandboxFileSizeError(
                f"File size ({file_size} bytes) exceeds maximum allowed size ({max_size} bytes). "
                "TODO: Future versions will support streaming for larger files.",
                max_size=max_size,
                actual_size=file_size,
            )

        # Read and encode file
        try:
            with open(local_path, "rb") as f:
                content_bytes = f.read()
            content_b64 = base64.b64encode(content_bytes).decode("utf-8")
        except IOError as e:
            raise SandboxFileError(f"Failed to read local file: {e}") from e

        # Upload file
        payload = {"path": remote_path, "content": content_b64, "overwrite": overwrite}

        try:
            response = self._client.put(
                f"{self._sandboxes_url}/{self.sandbox_id}/files",
                json=payload,
                timeout=35.0,  # Generous timeout for file operations
            )
            response.raise_for_status()
            data = response.json()

            # Handle error responses
            if "error" in data:
                if "not found" in data["error"].lower():
                    raise SandboxNotFoundError(f"Sandbox {self.sandbox_id} not found")
                raise SandboxFileError(f"File upload failed: {data['error']}")

            # Handle file exists case (silent failure)
            if not data.get("success", False) and data.get("exists", False):
                return False

            return data.get("success", False)

        except httpx.HTTPStatusError as e:
            self._handle_http_error(e, "upload file")

        except httpx.TimeoutException as e:
            raise SandboxTimeoutError(
                "File upload timed out", timeout_ms=35000, operation="upload"
            ) from e
        except httpx.RequestError as e:
            raise SandboxConnectionError(f"Failed to connect to sandbox service: {e}") from e

    def download_file(self, remote_path: str, local_path: str, overwrite: bool = False) -> bool:
        """
        Download a file from the sandbox to local filesystem.

        Args:
            remote_path: Absolute path in the sandbox to download from (must start with /)
            local_path: Path on local filesystem where file should be saved
            overwrite: If False (default), returns False when local file exists. If True, overwrites existing file.

        Returns:
            True if download was successful, False if file exists and overwrite=False

        Raises:
            SandboxFileNotFoundError: If remote file doesn't exist
            SandboxValidationError: If remote_path is not absolute
            SandboxNotFoundError: If sandbox is not found
            SandboxTimeoutError: If download times out
            SandboxFileError: If download fails for other reasons

        Example:
            # Download a result file (won't overwrite if exists)
            sbx.download_file("/tmp/output.txt", "./results/output.txt")
            
            # Download and overwrite if exists
            sbx.download_file("/home/user/data.csv", "./data.csv", overwrite=True)

        Note:
            TODO: Temporary 4MB limit. Future versions will support streaming for larger files.
        """
        import base64

        # Validate remote path is absolute
        if not remote_path.startswith("/"):
            raise SandboxValidationError("Remote path must be absolute (start with /)")

        # Check if local file exists and overwrite is disabled
        if os.path.exists(local_path) and not overwrite:
            return False

        # Create parent directory if needed
        local_dir = os.path.dirname(local_path)
        if local_dir:
            os.makedirs(local_dir, exist_ok=True)

        # Download file
        try:
            response = self._client.get(
                f"{self._sandboxes_url}/{self.sandbox_id}/files",
                params={"path": remote_path},
                timeout=35.0,  # Generous timeout for file operations
            )
            response.raise_for_status()
            data = response.json()

            # Handle error responses
            if "error" in data:
                if "not found" in data["error"].lower():
                    if "sandbox" in data["error"].lower():
                        raise SandboxNotFoundError(f"Sandbox {self.sandbox_id} not found")
                    else:
                        raise SandboxFileNotFoundError(
                            f"Remote file not found: {remote_path}",
                            path=remote_path,
                            is_local=False,
                        )
                raise SandboxFileError(f"File download failed: {data['error']}")

            # Decode and write file
            if "content" not in data:
                raise SandboxInvalidResponseError("Response missing 'content' field")

            try:
                content_bytes = base64.b64decode(data["content"])
                with open(local_path, "wb") as f:
                    f.write(content_bytes)
                return True
            except (IOError, OSError) as e:
                raise SandboxFileError(f"Failed to write local file: {e}") from e

        except httpx.HTTPStatusError as e:
            self._handle_http_error(e, "download file")

        except httpx.TimeoutException as e:
            raise SandboxTimeoutError(
                "File download timed out", timeout_ms=35000, operation="download"
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
def sandbox(name: str = "sandbox", internet_access: bool = True):
    """
    Context manager for creating and automatically cleaning up a sandbox.

    This provides a cleaner way to work with sandboxes by automatically
    handling creation and deletion using Python's with statement.

    Args:
        name: Human-readable name for the sandbox (default: "sandbox")
        internet_access: Enable internet access for the sandbox (default: True)

    Yields:
        Sandbox: A sandbox instance ready for code execution

    Raises:
        SandboxCreationError: If sandbox creation fails
        ValueError: If CONCAVE_SANDBOX_API_KEY environment variable is not set

    Example:
        ```python
        from concave import sandbox

        with sandbox(name="my-test") as s:
            result = s.run("print('Hello from Concave!')")
            print(result.stdout)
        # Sandbox is automatically deleted after the with block
        
        # Create sandbox without internet access
        with sandbox(name="isolated", internet_access=False) as s:
            result = s.run("print('No internet here!')")
            print(result.stdout)
        ```
    """
    sbx = Sandbox.create(name=name, internet_access=internet_access)
    try:
        yield sbx
    finally:
        sbx.delete()
        sbx._client.close()

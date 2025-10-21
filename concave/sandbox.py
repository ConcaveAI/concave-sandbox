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


class _ClassOnlyMethodDescriptor:
    """Descriptor that makes get() accessible only as a class method."""

    def __init__(self, method):
        self.method = method

    def __get__(self, instance, owner):
        if instance is not None:
            raise AttributeError(
                "get() is a class method only. Use Sandbox.get(sandbox_id) instead of sbx.get(sandbox_id)"
            )
        return self.method.__get__(None, owner)


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

    def __init__(
        self,
        id: str,
        base_url: str,
        api_key: Optional[str] = None,
        started_at: Optional[float] = None,
        metadata: Optional[dict[str, str]] = None,
    ):
        """
        Initialize a Sandbox instance.

        Args:
            id: Unique identifier for the sandbox (UUID)
            base_url: Base URL of the sandbox service
            api_key: API key for authentication
            started_at: Unix timestamp when sandbox was created (float)
            metadata: Immutable metadata attached to the sandbox

        Note:
            This constructor should not be called directly. Use Sandbox.create() instead.
        """
        self.id = id
        self.base_url = base_url.rstrip("/")
        self.started_at = started_at if started_at is not None else time.time()
        self.metadata = metadata
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
            sbx = Sandbox.create()
            sandbox_id = sbx.id
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

            # Parse started_at timestamp
            started_at = None
            if "started_at" in sandbox_data:
                started_at_value = sandbox_data["started_at"]
                # Handle both ISO string and numeric timestamp formats
                if isinstance(started_at_value, str):
                    from datetime import datetime
                    try:
                        dt = datetime.fromisoformat(started_at_value.replace('Z', '+00:00'))
                        started_at = dt.timestamp()
                    except (ValueError, AttributeError):
                        started_at = None
                elif isinstance(started_at_value, (int, float)):
                    started_at = float(started_at_value)

            # Create and return Sandbox instance
            return cls(
                id=sandbox_id,
                base_url=base_url,
                api_key=api_key,
                started_at=started_at,
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

    # Apply descriptor to make get() class-only
    get = _ClassOnlyMethodDescriptor(get)

    @classmethod
    def create(
        cls, internet_access: bool = True, metadata: Optional[dict[str, str]] = None
    ) -> "Sandbox":
        """
        Create a new sandbox instance.

        Args:
            internet_access: Enable internet access for the sandbox (default: True)
            metadata: Optional immutable metadata to attach (key-value pairs)

        Returns:
            A new Sandbox instance ready for code execution

        Raises:
            SandboxCreationError: If sandbox creation fails
            SandboxValidationError: If metadata validation fails
            ValueError: If CONCAVE_SANDBOX_API_KEY environment variable is not set

        Example:
            sbx = Sandbox.create()
            sbx_no_internet = Sandbox.create(internet_access=False)
            sbx_with_meta = Sandbox.create(metadata={"env": "prod", "user": "123"})
        """
        # Get credentials using helper method
        base_url, api_key = cls._get_credentials(None, None)

        # Validate metadata if provided
        if metadata is not None:
            import re
            
            # Validate key count
            if len(metadata) > 32:
                raise SandboxValidationError("metadata cannot have more than 32 keys")
            
            # Validate keys and values
            total_size = 0
            key_regex = re.compile(r'^[A-Za-z0-9_.-]+$')
            for key, value in metadata.items():
                # Validate key
                if not isinstance(key, str) or len(key) < 1 or len(key) > 64:
                    raise SandboxValidationError(f"metadata key '{key}' must be a string between 1 and 64 characters")
                if not key_regex.match(key):
                    raise SandboxValidationError(f"metadata key '{key}' contains invalid characters (only A-Za-z0-9_.- allowed)")
                
                # Validate value
                if not isinstance(value, str):
                    raise SandboxValidationError(f"metadata value for key '{key}' must be a string")
                value_bytes = len(value.encode('utf-8'))
                if value_bytes > 1024:
                    raise SandboxValidationError(f"metadata value for key '{key}' exceeds 1024 bytes")
                if '\x00' in value:
                    raise SandboxValidationError(f"metadata value for key '{key}' contains NUL byte")
                
                total_size += len(key.encode('utf-8')) + value_bytes
            
            # Validate total size
            if total_size > 4096:
                raise SandboxValidationError(f"total metadata size ({total_size} bytes) exceeds limit of 4096 bytes")

        # Create HTTP client using helper method
        client = cls._create_http_client(api_key)

        try:
            # Make creation request to the sandbox service
            base = base_url.rstrip("/")
            payload = {"internet_access": internet_access}
            if metadata:
                payload["metadata"] = metadata
            response = client.put(f"{base}/api/v1/sandboxes", json=payload)
            response.raise_for_status()
            sandbox_data = response.json()

            # Validate response contains required fields
            if "id" not in sandbox_data:
                raise SandboxInvalidResponseError(
                    f"Invalid response from sandbox service: {sandbox_data}"
                )

            sandbox_id = sandbox_data["id"]
            
            # Parse started_at timestamp
            started_at = None
            if "started_at" in sandbox_data:
                started_at_value = sandbox_data["started_at"]
                # Handle both ISO string and numeric timestamp formats
                if isinstance(started_at_value, str):
                    from datetime import datetime
                    try:
                        dt = datetime.fromisoformat(started_at_value.replace('Z', '+00:00'))
                        started_at = dt.timestamp()
                    except (ValueError, AttributeError):
                        started_at = None
                elif isinstance(started_at_value, (int, float)):
                    started_at = float(started_at_value)
            
            # Extract metadata from response
            response_metadata = sandbox_data.get("metadata")
            
            # Validate metadata was stored if we sent it
            if metadata and response_metadata != metadata:
                raise SandboxInvalidResponseError(
                    f"Server metadata mismatch: expected {metadata}, got {response_metadata}"
                )
            
            return cls(sandbox_id, base_url, api_key, started_at, response_metadata)

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
        internet_access: Optional[bool] = None,
        min_exec_count: Optional[int] = None,
        max_exec_count: Optional[int] = None,
        metadata_exists: Optional[list[str]] = None,
        metadata_equals: Optional[dict[str, str]] = None,
    ) -> dict:
        """
        List sandboxes with pagination metadata (single page).

        Returns a dictionary with sandboxes and pagination info for manual cursor-based pagination.

        Args:
            limit: Maximum number of sandboxes to return (default: 100)
            cursor: Pagination cursor for fetching next page
            since: Unix timestamp (epoch seconds) - only return sandboxes created at or after this time
            until: Unix timestamp (epoch seconds) - only return sandboxes created before this time
            internet_access: Filter by internet access (True/False)
            min_exec_count: Minimum number of executions
            max_exec_count: Maximum number of executions
            metadata_exists: List of metadata keys that must exist
            metadata_equals: Dict of key-value pairs that must match exactly

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
            
            # Filter by internet access and executions
            active = Sandbox.list_page(internet_access=True, min_exec_count=5)
            
            # Filter by metadata
            prod_sandboxes = Sandbox.list_page(metadata_equals={"env": "prod"})
        """
        # Get credentials using helper method
        base_url, api_key = cls._get_credentials(None, None)

        # Create HTTP client using helper method
        client = cls._create_http_client(api_key)

        try:
            # Validate metadata filters
            if metadata_exists:
                import re
                key_regex = re.compile(r'^[A-Za-z0-9_.-]+$')
                for key in metadata_exists:
                    if not isinstance(key, str) or len(key) < 1 or len(key) > 64:
                        raise SandboxValidationError(f"metadata_exists key '{key}' must be a string between 1 and 64 characters")
                    if not key_regex.match(key):
                        raise SandboxValidationError(f"metadata_exists key '{key}' contains invalid characters (only A-Za-z0-9_.- allowed)")
            
            if metadata_equals:
                import re
                key_regex = re.compile(r'^[A-Za-z0-9_.-]+$')
                for key, value in metadata_equals.items():
                    if not isinstance(key, str) or len(key) < 1 or len(key) > 64:
                        raise SandboxValidationError(f"metadata_equals key '{key}' must be a string between 1 and 64 characters")
                    if ':' in key:
                        raise SandboxValidationError(f"metadata_equals key '{key}' cannot contain colon character")
                    if not key_regex.match(key):
                        raise SandboxValidationError(f"metadata_equals key '{key}' contains invalid characters (only A-Za-z0-9_.- allowed)")
                    if not isinstance(value, str):
                        raise SandboxValidationError(f"metadata_equals value for key '{key}' must be a string")
                    if len(value.encode('utf-8')) > 1024:
                        raise SandboxValidationError(f"metadata_equals value for key '{key}' exceeds 1024 bytes")

            # Build query params
            params = []
            params.append(("limit", str(limit)))
            if cursor:
                params.append(("cursor", cursor))
            if since is not None:
                params.append(("since", str(since)))
            if until is not None:
                params.append(("until", str(until)))
            if internet_access is not None:
                params.append(("internet_access", "true" if internet_access else "false"))
            if min_exec_count is not None:
                params.append(("min_exec_count", str(min_exec_count)))
            if max_exec_count is not None:
                params.append(("max_exec_count", str(max_exec_count)))
            
            # Add metadata filters (repeatable query params)
            if metadata_exists:
                for key in metadata_exists:
                    params.append(("metadata_exists", key))
            
            if metadata_equals:
                for key, value in metadata_equals.items():
                    params.append(("metadata_equals", f"{key}:{value}"))

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
                    # Parse started_at timestamp
                    started_at = None
                    if "started_at" in sandbox_dict:
                        started_at_value = sandbox_dict["started_at"]
                        if isinstance(started_at_value, str):
                            from datetime import datetime
                            try:
                                dt = datetime.fromisoformat(started_at_value.replace('Z', '+00:00'))
                                started_at = dt.timestamp()
                            except (ValueError, AttributeError):
                                started_at = None
                        elif isinstance(started_at_value, (int, float)):
                            started_at = float(started_at_value)
                    
                    sandbox = cls(
                        id=sandbox_id,
                        base_url=base_url,
                        api_key=api_key,
                        started_at=started_at,
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
        internet_access: Optional[bool] = None,
        min_exec_count: Optional[int] = None,
        max_exec_count: Optional[int] = None,
        metadata_exists: Optional[list[str]] = None,
        metadata_equals: Optional[dict[str, str]] = None,
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
            internet_access: Filter by internet access (True/False)
            min_exec_count: Minimum number of executions
            max_exec_count: Maximum number of executions
            metadata_exists: List of metadata keys that must exist
            metadata_equals: Dict of key-value pairs that must match exactly

        Returns:
            List of Sandbox instances representing active sandboxes, sorted by newest first

        Raises:
            SandboxAuthenticationError: If authentication fails
            SandboxValidationError: If metadata filters are invalid
            ValueError: If CONCAVE_SANDBOX_API_KEY environment variable is not set

        Example:
            # List all sandboxes (auto-paginates)
            sandboxes = Sandbox.list()
            print(f"Found {len(sandboxes)} active sandboxes")
            
            # List sandboxes with internet and at least 5 executions
            active = Sandbox.list(internet_access=True, min_exec_count=5)

            # List sandboxes with time filter (epoch seconds)
            import time
            one_hour_ago = int(time.time()) - 3600
            recent_sandboxes = Sandbox.list(since=one_hour_ago)
            
            # List sandboxes with metadata filters
            prod_sandboxes = Sandbox.list(metadata_equals={"env": "prod"})
        """
        # Get credentials using helper method
        base_url, api_key = cls._get_credentials(None, None)

        # Create HTTP client using helper method
        client = cls._create_http_client(api_key)

        # Validate metadata filters
        if metadata_exists:
            import re
            key_regex = re.compile(r'^[A-Za-z0-9_.-]+$')
            for key in metadata_exists:
                if not isinstance(key, str) or len(key) < 1 or len(key) > 64:
                    raise SandboxValidationError(f"metadata_exists key '{key}' must be a string between 1 and 64 characters")
                if not key_regex.match(key):
                    raise SandboxValidationError(f"metadata_exists key '{key}' contains invalid characters (only A-Za-z0-9_.- allowed)")
        
        if metadata_equals:
            import re
            key_regex = re.compile(r'^[A-Za-z0-9_.-]+$')
            for key, value in metadata_equals.items():
                if not isinstance(key, str) or len(key) < 1 or len(key) > 64:
                    raise SandboxValidationError(f"metadata_equals key '{key}' must be a string between 1 and 64 characters")
                if ':' in key:
                    raise SandboxValidationError(f"metadata_equals key '{key}' cannot contain colon character")
                if not key_regex.match(key):
                    raise SandboxValidationError(f"metadata_equals key '{key}' contains invalid characters (only A-Za-z0-9_.- allowed)")
                if not isinstance(value, str):
                    raise SandboxValidationError(f"metadata_equals value for key '{key}' must be a string")
                if len(value.encode('utf-8')) > 1024:
                    raise SandboxValidationError(f"metadata_equals value for key '{key}' exceeds 1024 bytes")

        # Auto-pagination: if limit is None, fetch all pages
        if limit is None:
            all_sandboxes = []
            current_cursor = cursor
            
            while True:
                # Build query params
                params = []
                if current_cursor:
                    params.append(("cursor", current_cursor))
                if since is not None:
                    params.append(("since", str(since)))
                if until is not None:
                    params.append(("until", str(until)))
                if internet_access is not None:
                    params.append(("internet_access", "true" if internet_access else "false"))
                if min_exec_count is not None:
                    params.append(("min_exec_count", str(min_exec_count)))
                if max_exec_count is not None:
                    params.append(("max_exec_count", str(max_exec_count)))
                
                # Add metadata filters
                if metadata_exists:
                    for key in metadata_exists:
                        params.append(("metadata_exists", key))
                if metadata_equals:
                    for key, value in metadata_equals.items():
                        params.append(("metadata_equals", f"{key}:{value}"))

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
                            # Parse started_at timestamp
                            started_at = None
                            if "started_at" in sandbox_dict:
                                started_at_value = sandbox_dict["started_at"]
                                if isinstance(started_at_value, str):
                                    from datetime import datetime
                                    try:
                                        dt = datetime.fromisoformat(started_at_value.replace('Z', '+00:00'))
                                        started_at = dt.timestamp()
                                    except (ValueError, AttributeError):
                                        started_at = None
                                elif isinstance(started_at_value, (int, float)):
                                    started_at = float(started_at_value)
                            
                            sandbox = cls(
                                id=sandbox_id,
                                base_url=base_url,
                                api_key=api_key,
                                started_at=started_at,
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
            params = []
            params.append(("limit", str(limit)))
            if cursor:
                params.append(("cursor", cursor))
            if since is not None:
                params.append(("since", str(since)))
            if until is not None:
                params.append(("until", str(until)))
            if internet_access is not None:
                params.append(("internet_access", "true" if internet_access else "false"))
            if min_exec_count is not None:
                params.append(("min_exec_count", str(min_exec_count)))
            if max_exec_count is not None:
                params.append(("max_exec_count", str(max_exec_count)))
            
            # Add metadata filters
            if metadata_exists:
                for key in metadata_exists:
                    params.append(("metadata_exists", key))
            if metadata_equals:
                for key, value in metadata_equals.items():
                    params.append(("metadata_equals", f"{key}:{value}"))

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
                    # Parse started_at timestamp
                    started_at = None
                    if "started_at" in sandbox_dict:
                        started_at_value = sandbox_dict["started_at"]
                        if isinstance(started_at_value, str):
                            from datetime import datetime
                            try:
                                dt = datetime.fromisoformat(started_at_value.replace('Z', '+00:00'))
                                started_at = dt.timestamp()
                            except (ValueError, AttributeError):
                                started_at = None
                        elif isinstance(started_at_value, (int, float)):
                            started_at = float(started_at_value)
                    
                    sandbox = cls(
                        id=sandbox_id,
                        base_url=base_url,
                        api_key=api_key,
                        started_at=started_at,
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

        # Default timeout to 10000ms (10 seconds) if not specified
        if timeout is None:
            timeout = 10000

        # Prepare request payload
        payload = {"command": command, "timeout": timeout}

        # Set per-request timeout (ms to seconds + buffer)
        request_timeout = 12.0  # default: 10s + 2s buffer
        if timeout is not None and timeout > 0:
            request_timeout = (timeout / 1000.0) + 2.0

        try:
            response = self._client.post(
                f"{self._sandboxes_url}/{self.id}/exec",
                json=payload,
                timeout=request_timeout,
            )
            response.raise_for_status()
            data = response.json()

            # Handle error responses from the service
            if "error" in data:
                if "sandbox not found" in data["error"].lower():
                    raise SandboxNotFoundError(f"Sandbox {self.id} not found")
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

        # Default timeout to 10000ms (10 seconds) if not specified
        if timeout is None:
            timeout = 10000

        # Prepare request payload
        request_data = {"code": code, "language": language, "timeout": timeout}

        # Set per-request timeout (ms to seconds + buffer)
        request_timeout = 12.0  # default: 10s + 2s buffer
        if timeout is not None and timeout > 0:
            request_timeout = (timeout / 1000.0) + 2.0

        try:
            response = self._client.post(
                f"{self._sandboxes_url}/{self.id}/run",
                json=request_data,
                timeout=request_timeout,
            )
            response.raise_for_status()
            data = response.json()

            # Handle error responses from the service
            if "error" in data:
                if "sandbox not found" in data["error"].lower():
                    raise SandboxNotFoundError(f"Sandbox {self.id} not found")
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
            True if deletion was successful or sandbox already deleted, False otherwise

        Example:
            success = sbx.delete()
            if success:
                print("Sandbox deleted successfully")

        Note:
            After calling delete(), this Sandbox instance should not be used
            for further operations as the underlying sandbox will be destroyed.
        """
        try:
            response = self._client.delete(f"{self._sandboxes_url}/{self.id}")
            response.raise_for_status()
            data = response.json()

            # Check if deletion was successful
            return data.get("status") == "deleted"

        except httpx.HTTPStatusError as e:
            # 404 means sandbox not found - already deleted, so return True
            if e.response.status_code == 404:
                return True
            # 502 means backend timeout - deletion may still succeed, so return True
            # The sandbox will be cleaned up eventually by the backend
            if e.response.status_code == 502:
                return True
            # Other errors - return False
            return False
        except httpx.RequestError:
            # Network errors - return False
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
                f"{self._sandboxes_url}/{self.id}/ping",
                timeout=5.0,
            )
            response.raise_for_status()
            data = response.json()

            return data.get("status") == "ok"

        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            if status_code == 404:
                raise SandboxNotFoundError(f"Sandbox {self.id} not found") from e
            elif status_code == 401 or status_code == 403:
                raise SandboxAuthenticationError("Authentication failed") from e
            elif status_code == 502 or status_code == 503:
                raise SandboxUnavailableError(
                    f"Sandbox {self.id} is not ready or unreachable", status_code
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
                f"{self._sandboxes_url}/{self.id}/uptime",
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
            response = self._client.get(f"{self._sandboxes_url}/{self.id}")
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

    @property
    def info(self) -> Dict[str, Any]:
        """
        Get comprehensive sandbox information including id, started_at, and backend status.

        This property combines locally-stored attributes (id, started_at) with
        real-time status information from the backend (state, IP, exec_count, etc.).

        Returns:
            Dictionary containing:
            - id: Sandbox identifier (str)
            - started_at: Creation timestamp as float (Unix epoch)
            - user_id: User who owns the sandbox (str)
            - ip: Sandbox IP address (str)
            - state: Current sandbox state (str)
            - exec_count: Number of commands executed (int)
            - internet_access: Whether internet is enabled (bool)

        Raises:
            SandboxNotFoundError: If the sandbox no longer exists
            SandboxTimeoutError: If the request times out
            SandboxConnectionError: If unable to connect to the service

        Example:
            sbx = Sandbox.create()
            info = sbx.info
            print(f"Sandbox {info['id']} created at {info['started_at']}")
            print(f"State: {info['state']}, IP: {info['ip']}")
            print(f"Executed {info['exec_count']} commands")
        """
        status_data = self.status()
        return {
            'id': self.id,
            'started_at': self.started_at,
            **status_data
        }

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
                f"{self._sandboxes_url}/{self.id}/files",
                json=payload,
                timeout=35.0,  # Generous timeout for file operations
            )
            response.raise_for_status()
            data = response.json()

            # Handle error responses
            if "error" in data:
                if "not found" in data["error"].lower():
                    raise SandboxNotFoundError(f"Sandbox {self.id} not found")
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
                f"{self._sandboxes_url}/{self.id}/files",
                params={"path": remote_path},
                timeout=35.0,  # Generous timeout for file operations
            )
            response.raise_for_status()
            data = response.json()

            # Handle error responses
            if "error" in data:
                if "not found" in data["error"].lower():
                    if "sandbox" in data["error"].lower():
                        raise SandboxNotFoundError(f"Sandbox {self.id} not found")
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
        return f"Sandbox(id={self.id}, started_at={self.started_at})"


@contextmanager
def sandbox(internet_access: bool = True, metadata: Optional[dict[str, str]] = None):
    """
    Context manager for creating and automatically cleaning up a sandbox.

    This provides a cleaner way to work with sandboxes by automatically
    handling creation and deletion using Python's with statement.

    Args:
        internet_access: Enable internet access for the sandbox (default: True)
        metadata: Optional immutable metadata to attach (key-value pairs)

    Yields:
        Sandbox: A sandbox instance ready for code execution

    Raises:
        SandboxCreationError: If sandbox creation fails
        SandboxValidationError: If metadata validation fails
        ValueError: If CONCAVE_SANDBOX_API_KEY environment variable is not set

    Example:
        ```python
        from concave import sandbox

        with sandbox() as s:
            result = s.run("print('Hello from Concave!')")
            print(result.stdout)
        # Sandbox is automatically deleted after the with block
        
        # Create sandbox without internet access
        with sandbox(internet_access=False) as s:
            result = s.run("print('No internet here!')")
            print(result.stdout)
        
        # Create sandbox with metadata
        with sandbox(metadata={"env": "prod", "user": "123"}) as s:
            result = s.run("print('Tracked sandbox!')")
            print(result.stdout)
        ```
    """
    sbx = Sandbox.create(internet_access=internet_access, metadata=metadata)
    try:
        yield sbx
    finally:
        sbx.delete()
        sbx._client.close()

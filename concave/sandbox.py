"""
Sandbox client implementation for the Concave service.

This module provides the core Sandbox class that manages sandbox lifecycle and
code execution through the Concave sandbox API. It handles HTTP communication,
error management, and provides a clean interface for sandbox operations.
"""

import os
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional, Callable, List
import json

import httpx

from . import __version__
from .exceptions import *
from .results import ExecuteResult, RunResult, SandboxList
from .namespaces import FilesNamespace, NetworkNamespace, MonitorNamespace


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
    providing methods to create, execute commands, run Python and JavaScript code, and clean up
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
        elif status_code == 408:
            # Request timeout from backend
            raise SandboxTimeoutError(f"Operation timed out: {error_msg}", timeout_ms=None, operation=operation) from e
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
        
        # Initialize namespaces
        self.files = FilesNamespace(self)
        self.network = NetworkNamespace(self)
        self.monitor = MonitorNamespace(self)

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
        cls, 
        internet_access: bool = True, 
        metadata: Optional[dict[str, str]] = None,
        env: Optional[dict[str, str]] = None,
        lifetime: Optional[str] = None
    ) -> "Sandbox":
        """
        Create a new sandbox instance.

        Args:
            internet_access: Enable internet access for the sandbox (default: True)
            metadata: Optional immutable metadata to attach (key-value pairs)
            env: Optional custom environment variables to inject into the sandbox
            lifetime: Optional sandbox lifetime (e.g., "1h", "30m", "1h30m"). 
                     Min: 1m, Max: 48h, Default: 24h

        Returns:
            A new Sandbox instance ready for code execution

        Raises:
            SandboxCreationError: If sandbox creation fails
            SandboxValidationError: If metadata, env, or lifetime validation fails
            ValueError: If CONCAVE_SANDBOX_API_KEY environment variable is not set

        Example:
            sbx = Sandbox.create()
            sbx_no_internet = Sandbox.create(internet_access=False)
            sbx_with_meta = Sandbox.create(metadata={"env": "prod", "user": "123"})
            sbx_with_env = Sandbox.create(env={"API_KEY": "secret", "DEBUG": "true"})
            sbx_short_lived = Sandbox.create(lifetime="1h")
            sbx_long_lived = Sandbox.create(lifetime="48h")
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

        # Validate env if provided
        if env is not None:
            import re
            
            # Validate key count
            if len(env) > 32:
                raise SandboxValidationError("env cannot have more than 32 keys")
            
            # Validate keys and values (Linux env var naming rules)
            total_size = 0
            key_regex = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
            for key, value in env.items():
                # Validate key
                if not isinstance(key, str) or len(key) < 1 or len(key) > 64:
                    raise SandboxValidationError(f"env key '{key}' must be a string between 1 and 64 characters")
                if not key_regex.match(key):
                    raise SandboxValidationError(f"env key '{key}' contains invalid characters (must start with letter or underscore, followed by alphanumeric or underscore)")
                
                # Validate value
                if not isinstance(value, str):
                    raise SandboxValidationError(f"env value for key '{key}' must be a string")
                value_bytes = len(value.encode('utf-8'))
                if value_bytes > 1024:
                    raise SandboxValidationError(f"env value for key '{key}' exceeds 1024 bytes")
                if '\x00' in value:
                    raise SandboxValidationError(f"env value for key '{key}' contains NUL byte")
                
                total_size += len(key.encode('utf-8')) + value_bytes
            
            # Validate total size
            if total_size > 4096:
                raise SandboxValidationError(f"total env size ({total_size} bytes) exceeds limit of 4096 bytes")

        # Validate lifetime if provided
        if lifetime is not None:
            import re
            
            if not isinstance(lifetime, str):
                raise SandboxValidationError("lifetime must be a string")
            
            # Validate format: must match pattern like "1h", "30m", "1h30m", "90s"
            # Pattern: optional hours, optional minutes, optional seconds (at least one required)
            lifetime_regex = re.compile(r'^((\d+h)?(\d+m)?(\d+s)?|(\d+h\d+m\d+s)|(\d+h\d+m)|(\d+h\d+s)|(\d+m\d+s))$')
            if not lifetime_regex.match(lifetime) or lifetime == "":
                raise SandboxValidationError(f"lifetime '{lifetime}' has invalid format (use formats like '1h', '30m', '1h30m', '90s')")
            
            # Check if it contains at least one time component
            if not any(c in lifetime for c in ['h', 'm', 's']):
                raise SandboxValidationError(f"lifetime '{lifetime}' must contain at least one time unit (h, m, or s)")

        # Create HTTP client using helper method
        client = cls._create_http_client(api_key)

        try:
            # Make creation request to the sandbox service
            base = base_url.rstrip("/")
            payload = {"internet_access": internet_access}
            if metadata:
                payload["metadata"] = metadata
            if env:
                payload["env"] = env
            if lifetime:
                payload["lifetime"] = lifetime
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
    def list(
        cls,
        limit: int = 10,
        cursor: Optional[str] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        internet_access: Optional[bool] = None,
        min_exec_count: Optional[int] = None,
        max_exec_count: Optional[int] = None,
        metadata_exists: Optional[list[str]] = None,
        metadata_equals: Optional[dict[str, str]] = None,
    ) -> SandboxList:
        """
        List active sandboxes for the authenticated user (single page).

        Returns a SandboxList object containing sandboxes and pagination metadata.
        Always returns a single page of results - use the pagination metadata to
        fetch subsequent pages if needed.

        Args:
            limit: Maximum number of sandboxes to return (default: 10, max: 100)
            cursor: Pagination cursor for fetching next page
            since: Unix timestamp (epoch seconds) - only return sandboxes created at or after this time
            until: Unix timestamp (epoch seconds) - only return sandboxes created before this time
            internet_access: Filter by internet access (True/False)
            min_exec_count: Minimum number of executions
            max_exec_count: Maximum number of executions
            metadata_exists: List of metadata keys that must exist
            metadata_equals: Dict of key-value pairs that must match exactly

        Returns:
            SandboxList: List-like object with sandboxes and pagination metadata:
                - Items are Sandbox instances (iterate like a normal list)
                - .has_more: Boolean indicating if more pages exist
                - .next_cursor: String cursor for next page (None if no more)
                - .count: Number of sandboxes in this page

        Raises:
            SandboxAuthenticationError: If authentication fails
            SandboxValidationError: If limit > 100 or metadata filters are invalid
            ValueError: If CONCAVE_SANDBOX_API_KEY environment variable is not set

        Example:
            # List first 10 sandboxes (default)
            sandboxes = Sandbox.list()
            for sbx in sandboxes:
                print(sbx.id)
            
            # Check if there are more and fetch next page
            if sandboxes.has_more:
                next_page = Sandbox.list(cursor=sandboxes.next_cursor)

            # List with custom limit
            sandboxes = Sandbox.list(limit=50)
            
            # Filter sandboxes
            active = Sandbox.list(internet_access=True, min_exec_count=5)
            prod_sandboxes = Sandbox.list(metadata_equals={"env": "prod"})
        """
        # Validate limit
        if limit > 100:
            raise SandboxValidationError(f"Limit cannot exceed 100, got: {limit}")
        
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

            # Return SandboxList with pagination metadata
            return SandboxList(
                items=sandbox_instances,
                has_more=data.get('has_more', False),
                next_cursor=data.get('next_cursor'),
                count=data.get('count', len(sandbox_instances)),
            )

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

    def execute(
        self,
        command: str,
        timeout: Optional[int] = None,
        streaming: bool = False,
        callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> ExecuteResult:
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

        # Non-streaming path (JSON response)
        if not streaming:
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
                    error_msg = data["error"]
                    if "sandbox not found" in error_msg.lower():
                        raise SandboxNotFoundError(f"Sandbox {self.id} not found")
                    if "timed out" in error_msg.lower():
                        timeout_val = timeout if timeout else 10000
                        raise SandboxTimeoutError(error_msg, timeout_ms=timeout_val, operation="execute")
                    raise SandboxExecutionError(f"Execution failed: {error_msg}")

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

        # Streaming path (NDJSON events)
        # Default timeout to 300000ms (5 minutes) if not specified
        if timeout is None:
            timeout = 300000

        payload = {"command": command, "timeout": timeout}

        # Set per-request timeout (ms to seconds + buffer)
        request_timeout = None
        if timeout is not None and timeout > 0:
            request_timeout = (timeout / 1000.0) + 2.0

        try:
            stdout_parts: List[str] = []
            stderr_parts: List[str] = []
            exit_code: Optional[int] = None

            with self._client.stream(
                "POST",
                f"{self._sandboxes_url}/{self.id}/execute_stream",
                json=payload,
                timeout=request_timeout,
            ) as resp:
                if resp.status_code != 200:
                    raw = resp.read()
                    try:
                        data = resp.json()
                    except Exception:
                        try:
                            text = raw.decode("utf-8", errors="replace")
                        except Exception:
                            text = ""
                        data = {"error": text}
                    # Raise appropriate error based on response
                    error_msg = data.get("error") if isinstance(data, dict) else None
                    if isinstance(error_msg, str) and "sandbox not found" in error_msg.lower():
                        raise SandboxNotFoundError(f"Sandbox {self.id} not found")
                    # Handle timeout responses (408 Request Timeout)
                    if resp.status_code == 408:
                        timeout_val = timeout if timeout else 300000
                        raise SandboxTimeoutError(
                            f"Command execution timed out: {error_msg if error_msg else 'timeout'}",
                            timeout_ms=timeout_val,
                            operation="execute_stream"
                        )
                    raise SandboxExecutionError(
                        f"Execute streaming failed: {error_msg if error_msg else raw.decode('utf-8', errors='replace')}"
                    )

                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    etype = event.get("type")
                    if etype == "stdout":
                        data = event.get("data", "")
                        stdout_parts.append(data + "\n")
                        if callback:
                            try:
                                callback(event)
                            except Exception:
                                pass
                    elif etype == "stderr":
                        data = event.get("data", "")
                        stderr_parts.append(data + "\n")
                        if callback:
                            try:
                                callback(event)
                            except Exception:
                                pass
                    elif etype == "exit":
                        try:
                            exit_code = int(event.get("code", -1))
                        except Exception:
                            exit_code = -1
                        if callback:
                            try:
                                callback(event)
                            except Exception:
                                pass
                        break

            return ExecuteResult(
                stdout="".join(stdout_parts),
                stderr="".join(stderr_parts),
                returncode=exit_code if exit_code is not None else -1,
                command=command,
            )

        except httpx.HTTPStatusError as e:
            self._handle_http_error(e, "execute command (streaming)")
        except httpx.TimeoutException as e:
            timeout_val = timeout if timeout else 300000
            raise SandboxTimeoutError(
                "Command execution timed out", timeout_ms=timeout_val, operation="execute_stream"
            ) from e
        except httpx.RequestError as e:
            raise SandboxConnectionError(f"Failed to connect to sandbox service: {e}") from e

    def run(
        self,
        code: str,
        timeout: Optional[int] = None,
        language: str = "python",
        streaming: bool = False,
        callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> RunResult:
        """
        Run code in the sandbox with secure isolation.

        Args:
            code: Code to execute
            timeout: Timeout in milliseconds (default: 10000ms)
            language: Programming language to use (default: "python"). Supported: "python", "javascript".

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
            
            # Run JavaScript code
            result = sbx.run("console.log('Hello, World!')", language="javascript")
            print(result.stdout)  # Hello, World!
            
            # Run Python with timeout
            result = sbx.run("import time; time.sleep(1)", timeout=3000)
            print(result.stdout)
        """
        if not code.strip():
            raise SandboxValidationError("Code cannot be empty")

        if language not in ("python", "javascript"):
            raise SandboxValidationError(f"Unsupported language: {language}. Currently only 'python' and 'javascript' are supported.")

        # Non-streaming path (JSON response)
        if not streaming:
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
                    error_msg = data["error"]
                    if "sandbox not found" in error_msg.lower():
                        raise SandboxNotFoundError(f"Sandbox {self.id} not found")
                    if "timed out" in error_msg.lower():
                        timeout_val = timeout if timeout else 10000
                        raise SandboxTimeoutError(error_msg, timeout_ms=timeout_val, operation="run")
                    raise SandboxExecutionError(f"Code execution failed: {error_msg}")

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

        # Streaming path (NDJSON events)
        # Default timeout to 300000ms (5 minutes) if not specified
        if timeout is None:
            timeout = 300000

        payload = {"code": code, "language": language, "timeout": timeout}

        # Set per-request timeout (ms to seconds + buffer)
        request_timeout = None
        if timeout is not None and timeout > 0:
            request_timeout = (timeout / 1000.0) + 2.0

        try:
            stdout_parts: List[str] = []
            stderr_parts: List[str] = []
            exit_code: Optional[int] = None

            with self._client.stream(
                "POST",
                f"{self._sandboxes_url}/{self.id}/run_stream",
                json=payload,
                timeout=request_timeout,
            ) as resp:
                if resp.status_code != 200:
                    raw = resp.read()
                    try:
                        data = resp.json()
                    except Exception:
                        try:
                            text = raw.decode("utf-8", errors="replace")
                        except Exception:
                            text = ""
                        data = {"error": text}
                    # Raise appropriate error based on response
                    error_msg = data.get("error") if isinstance(data, dict) else None
                    if isinstance(error_msg, str) and "sandbox not found" in error_msg.lower():
                        raise SandboxNotFoundError(f"Sandbox {self.id} not found")
                    # Handle timeout responses (408 Request Timeout)
                    if resp.status_code == 408:
                        timeout_val = timeout if timeout else 300000
                        raise SandboxTimeoutError(
                            f"Code execution timed out: {error_msg if error_msg else 'timeout'}",
                            timeout_ms=timeout_val,
                            operation="run_stream"
                        )
                    raise SandboxExecutionError(
                        f"Run streaming failed: {error_msg if error_msg else raw.decode('utf-8', errors='replace')}"
                    )

                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    etype = event.get("type")
                    if etype == "stdout":
                        data = event.get("data", "")
                        stdout_parts.append(data + "\n")
                        if callback:
                            try:
                                callback(event)
                            except Exception:
                                pass
                    elif etype == "stderr":
                        data = event.get("data", "")
                        stderr_parts.append(data + "\n")
                        if callback:
                            try:
                                callback(event)
                            except Exception:
                                pass
                    elif etype == "exit":
                        try:
                            exit_code = int(event.get("code", -1))
                        except Exception:
                            exit_code = -1
                        if callback:
                            try:
                                callback(event)
                            except Exception:
                                pass
                        break

            return RunResult(
                stdout="".join(stdout_parts),
                stderr="".join(stderr_parts),
                returncode=exit_code if exit_code is not None else -1,
                code=code,
                language=language,
            )

        except httpx.HTTPStatusError as e:
            self._handle_http_error(e, "run code (streaming)")
        except httpx.TimeoutException as e:
            timeout_val = timeout if timeout else 300000
            raise SandboxTimeoutError(
                "Code execution timed out", timeout_ms=timeout_val, operation="run_stream"
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

    def pause(self) -> Dict[str, Any]:
        """
        Pause the sandbox VM execution.

        When paused, the Firecracker VM freezes in memory but the process stays alive.
        Memory remains allocated and the sandbox still counts toward concurrency limits.
        Only CPU execution is frozen.

        Returns:
            Dictionary containing:
            - sandbox_id: Sandbox identifier
            - paused: True
            - paused_at: Timestamp when paused

        Raises:
            SandboxNotFoundError: If the sandbox is not found
            SandboxClientError: If sandbox is already paused (409 Conflict)
            SandboxAuthenticationError: If authentication fails
            SandboxUnavailableError: If the sandbox is unavailable
            SandboxTimeoutError: If the pause request times out

        Example:
            sbx.pause()
            print("Sandbox paused - VM frozen but process still alive")
            # Later...
            sbx.resume()
        """
        try:
            response = self._client.post(
                f"{self._sandboxes_url}/{self.id}/pause",
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            if status_code == 409:
                raise SandboxClientError("Sandbox is already paused") from e
            self._handle_http_error(e, "pause")

        except httpx.TimeoutException as e:
            raise SandboxTimeoutError(
                "Pause request timed out", timeout_ms=10000, operation="pause"
            ) from e
        except httpx.RequestError as e:
            raise SandboxConnectionError(f"Failed to connect to sandbox service: {e}") from e

    def resume(self) -> Dict[str, Any]:
        """
        Resume a paused sandbox VM execution.

        Resumes a previously paused sandbox, allowing it to continue execution
        from where it left off.

        Returns:
            Dictionary containing:
            - sandbox_id: Sandbox identifier
            - paused: False

        Raises:
            SandboxNotFoundError: If the sandbox is not found
            SandboxClientError: If sandbox is not paused (409 Conflict)
            SandboxAuthenticationError: If authentication fails
            SandboxUnavailableError: If the sandbox is unavailable
            SandboxTimeoutError: If the resume request times out

        Example:
            sbx.pause()
            # Do something else...
            sbx.resume()
            # Sandbox continues from where it left off
        """
        try:
            response = self._client.post(
                f"{self._sandboxes_url}/{self.id}/resume",
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            if status_code == 409:
                raise SandboxClientError("Sandbox is not paused") from e
            self._handle_http_error(e, "resume")

        except httpx.TimeoutException as e:
            raise SandboxTimeoutError(
                "Resume request timed out", timeout_ms=10000, operation="resume"
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
def sandbox(
    internet_access: bool = True, 
    metadata: Optional[dict[str, str]] = None,
    env: Optional[dict[str, str]] = None,
    lifetime: Optional[str] = None
):
    """
    Context manager for creating and automatically cleaning up a sandbox.

    This provides a cleaner way to work with sandboxes by automatically
    handling creation and deletion using Python's with statement.

    Args:
        internet_access: Enable internet access for the sandbox (default: True)
        metadata: Optional immutable metadata to attach (key-value pairs)
        env: Optional custom environment variables to inject into the sandbox
        lifetime: Optional sandbox lifetime (e.g., "1h", "30m", "1h30m"). 
                 Min: 1m, Max: 48h, Default: 24h

    Yields:
        Sandbox: A sandbox instance ready for code execution

    Raises:
        SandboxCreationError: If sandbox creation fails
        SandboxValidationError: If metadata, env, or lifetime validation fails
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
        
        # Create sandbox with custom env vars
        with sandbox(env={"API_KEY": "secret", "DEBUG": "true"}) as s:
            result = s.run("import os; print(os.environ['API_KEY'])")
            print(result.stdout)
        
        # Create sandbox with custom lifetime
        with sandbox(lifetime="5h") as s:
            result = s.run("print('This sandbox lives for 5 hours!')")
            print(result.stdout)
        ```
    """
    sbx = Sandbox.create(internet_access=internet_access, metadata=metadata, env=env, lifetime=lifetime)
    try:
        yield sbx
    finally:
        sbx.delete()
        sbx._client.close()

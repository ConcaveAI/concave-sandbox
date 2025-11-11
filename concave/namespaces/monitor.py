"""
Monitor namespace for sandbox health and status operations.
"""

from typing import Any, Dict, TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from ..sandbox import Sandbox

from ..exceptions import (
    SandboxAuthenticationError,
    SandboxConnectionError,
    SandboxInvalidResponseError,
    SandboxNotFoundError,
    SandboxTimeoutError,
    SandboxUnavailableError,
)


class MonitorNamespace:
    """
    Namespace for sandbox monitoring operations.
    
    Provides methods to check sandbox health and retrieve status information:
    - ping(): Check if sandbox is responsive
    - uptime(): Get sandbox uptime in seconds
    - status(): Get current sandbox status
    - info(): Get comprehensive sandbox information
    """
    
    def __init__(self, sandbox: "Sandbox"):
        """
        Initialize the monitor namespace.
        
        Args:
            sandbox: Parent Sandbox instance
        """
        self._sandbox = sandbox
    
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
            if sbx.monitor.ping():
                print("Sandbox is alive!")
            else:
                print("Sandbox is not responding")
        """
        try:
            response = self._sandbox._client.get(
                f"{self._sandbox._sandboxes_url}/{self._sandbox.id}/ping",
                timeout=5.0,
            )
            response.raise_for_status()
            data = response.json()

            return data.get("status") == "ok"

        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            if status_code == 404:
                raise SandboxNotFoundError(f"Sandbox {self._sandbox.id} not found") from e
            elif status_code == 401 or status_code == 403:
                raise SandboxAuthenticationError("Authentication failed") from e
            elif status_code == 502 or status_code == 503:
                raise SandboxUnavailableError(
                    f"Sandbox {self._sandbox.id} is not ready or unreachable", status_code
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

        Example:
            uptime_seconds = sbx.monitor.uptime()
            print(f"Sandbox has been running for {uptime_seconds:.2f} seconds")
        """
        try:
            response = self._sandbox._client.get(
                f"{self._sandbox._sandboxes_url}/{self._sandbox.id}/uptime",
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
            self._sandbox._handle_http_error(e, "get uptime")

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

        Example:
            status = sbx.monitor.status()
            print(f"Sandbox State: {status['state']}")
            print(f"Commands executed: {status['exec_count']}")
            print(f"IP address: {status['ip']}")
        """
        try:
            response = self._sandbox._client.get(f"{self._sandbox._sandboxes_url}/{self._sandbox.id}")
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            self._sandbox._handle_http_error(e, "get status")

        except httpx.TimeoutException as e:
            raise SandboxTimeoutError(
                "Status check timed out", timeout_ms=5000, operation="status"
            ) from e
        except httpx.RequestError as e:
            raise SandboxConnectionError(f"Failed to connect to sandbox service: {e}") from e

    def info(self) -> Dict[str, Any]:
        """
        Get comprehensive sandbox information including id, started_at, and backend status.

        This combines locally-stored attributes (id, started_at) with
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
            info = sbx.monitor.info()
            print(f"Sandbox {info['id']} created at {info['started_at']}")
            print(f"State: {info['state']}, IP: {info['ip']}")
            print(f"Executed {info['exec_count']} commands")
        """
        status_data = self.status()
        return {
            'id': self._sandbox.id,
            'started_at': self._sandbox.started_at,
            **status_data
        }


"""
Network namespace for sandbox port publishing operations.
"""

from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from ..sandbox import Sandbox

from ..exceptions import (
    SandboxAuthenticationError,
    SandboxConnectionError,
    SandboxInvalidResponseError,
    SandboxNotFoundError,
    SandboxTimeoutError,
    SandboxValidationError,
)


class NetworkNamespace:
    """
    Namespace for sandbox network operations.
    
    Provides methods to expose and unexpose sandbox ports:
    - publish(): Expose a sandbox port via public URL
    - unpublish(): Remove public exposure of a port
    """
    
    def __init__(self, sandbox: "Sandbox"):
        """
        Initialize the network namespace.
        
        Args:
            sandbox: Parent Sandbox instance
        """
        self._sandbox = sandbox
    
    def publish(self, port: int) -> str:
        """
        Expose a sandbox port via a public *.concave.run URL.
        
        Args:
            port: Internal sandbox port to expose (e.g., 3000, 8000, 8080)
        
        Returns:
            str: Public URL to access the exposed port (e.g., "a1b2c3d4.concave.run")
        
        Raises:
            SandboxNotFoundError: If the sandbox doesn't exist
            SandboxValidationError: If port is invalid (not 1-65535)
            SandboxAuthenticationError: If API authentication fails
            SandboxServerError: If the expose service fails
            SandboxConnectionError: If unable to connect to the service
            SandboxTimeoutError: If the request times out
        
        Example:
            sbx = Sandbox.create()
            
            # Start a web server in the sandbox
            sbx.execute("nohup python3 -m http.server 8000 > /tmp/server.log 2>&1 &")
            
            # Expose it publicly
            url = sbx.network.publish(8000)
            print(f"Access your server at: https://{url}")
            # Output: Access your server at: https://a1b2c3d4.concave.run
            
            # Now anyone can access https://a1b2c3d4.concave.run
        
        Note:
            When a sandbox is deleted, all published ports are automatically
            unpublished and their *.concave.run URLs become invalid.
        """
        if not isinstance(port, int) or port < 1 or port > 65535:
            raise SandboxValidationError(f"Port must be an integer between 1 and 65535, got: {port}")
        
        try:
            response = self._sandbox._client.post(
                f"{self._sandbox._sandboxes_url}/{self._sandbox.id}/publish",
                json={"port": port},
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()
            
            # Extract URL from response
            if "url" not in data:
                raise SandboxInvalidResponseError(
                    f"Invalid publish response: missing 'url' field"
                )
            
            return data["url"]
        
        except httpx.HTTPStatusError as e:
            self._sandbox._handle_http_error(e, "publish port")
        except httpx.TimeoutException as e:
            raise SandboxTimeoutError(
                "Publish request timed out", timeout_ms=10000, operation="publish"
            ) from e
        except httpx.RequestError as e:
            raise SandboxConnectionError(f"Failed to connect to sandbox service: {e}") from e

    def unpublish(self, port: int) -> bool:
        """
        Remove public exposure of a sandbox port.
        
        Args:
            port: Internal sandbox port to unpublish
        
        Returns:
            bool: True if unpublish succeeded (always returns True unless error)
        
        Raises:
            SandboxNotFoundError: If the sandbox doesn't exist
            SandboxValidationError: If port is invalid
            SandboxAuthenticationError: If API authentication fails
            SandboxServerError: If the expose service fails
            SandboxConnectionError: If unable to connect to the service
            SandboxTimeoutError: If the request times out
        
        Note:
            Returns True even if the port was not published. Only returns False
            or raises exception on actual errors.
        
        Example:
            sbx = Sandbox.create()
            url = sbx.network.publish(8000)
            print(f"Published: {url}")
            
            # Later, remove the exposure
            success = sbx.network.unpublish(8000)
            print(f"Unpublished: {success}")  # True
            
            # Unpublishing again still returns True
            success = sbx.network.unpublish(8000)  # Still True
        """
        if not isinstance(port, int) or port < 1 or port > 65535:
            raise SandboxValidationError(f"Port must be an integer between 1 and 65535, got: {port}")
        
        try:
            response = self._sandbox._client.request(
                "DELETE",
                f"{self._sandbox._sandboxes_url}/{self._sandbox.id}/unpublish",
                json={"port": port},
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()
            
            # Check success field
            return data.get("success", True)
        
        except httpx.HTTPStatusError as e:
            self._sandbox._handle_http_error(e, "unpublish port")
        except httpx.TimeoutException as e:
            raise SandboxTimeoutError(
                "Unpublish request timed out", timeout_ms=10000, operation="unpublish"
            ) from e
        except httpx.RequestError as e:
            raise SandboxConnectionError(f"Failed to connect to sandbox service: {e}") from e


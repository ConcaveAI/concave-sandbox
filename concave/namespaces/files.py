"""
Files namespace for sandbox file operations.
"""

import json
import os
import re
from typing import TYPE_CHECKING, Optional

import httpx

if TYPE_CHECKING:
    from ..sandbox import Sandbox

from ..exceptions import (
    SandboxChecksumMismatchError,
    SandboxConnectionError,
    SandboxFileBusyError,
    SandboxFileError,
    SandboxFileExistsError,
    SandboxFileLockedError,
    SandboxFileNotFoundError,
    SandboxInsufficientStorageError,
    SandboxInvalidResponseError,
    SandboxPermissionDeniedError,
    SandboxTimeoutError,
    SandboxUnavailableError,
    SandboxUnsupportedMediaTypeError,
    SandboxValidationError,
)


def _extract_error_code_from_rpc_message(message: str) -> Optional[str]:
    """
    Extract error_code from nested RPC error messages.
    
    Some API errors return HTTP 502/503 with error details nested in the message like:
    "rpc error: code = Unknown desc = file write failed with status 409: {...json...}"
    
    This function extracts and parses the JSON to get the error_code.
    """
    if "error_code" not in message:
        return None
    
    # Look for JSON object in the message
    json_match = re.search(r'\{[^}]*"error_code"[^}]*\}', message)
    if not json_match:
        return None
    
    try:
        # The JSON might have escaped quotes
        json_str = json_match.group().replace('\\"', '"')
        nested = json.loads(json_str)
        return nested.get("error_code", "").upper()
    except (json.JSONDecodeError, ValueError):
        return None


class FilesNamespace:
    """
    Namespace for sandbox file operations.
    
    Provides methods for reading, writing, uploading, and downloading files:
    - read(): Read a file from the sandbox (up to 4MB)
    - write(): Write a string to a file in the sandbox (up to 4MB)
    - upload(): Upload a file from local filesystem to sandbox
    - download(): Download a file from sandbox to local filesystem
    """
    
    def __init__(self, sandbox: "Sandbox"):
        """
        Initialize the files namespace.
        
        Args:
            sandbox: Parent Sandbox instance
        """
        self._sandbox = sandbox
    
    @staticmethod
    def _raise_file_error_from_response(status_code: int, json_data: dict, default_message: str, path: str, op: str):
        """Helper method to raise appropriate file error based on response."""
        error_code = (json_data.get("error_code") or "").upper()
        message = json_data.get("error") or default_message
        if status_code == 404 or error_code == "FILE_NOT_FOUND":
            raise SandboxFileNotFoundError(message, path=path, is_local=False)
        if status_code == 409 or error_code == "FILE_EXISTS":
            raise SandboxFileExistsError(message, path=path)
        if status_code == 423:
            if error_code == "FILE_BUSY":
                raise SandboxFileBusyError(message, path=path)
            raise SandboxFileLockedError(message, path=path)
        if status_code == 507 or error_code == "INSUFFICIENT_STORAGE":
            raise SandboxInsufficientStorageError(message, path=path)
        if status_code == 403 or error_code == "PERMISSION_DENIED":
            raise SandboxPermissionDeniedError(message, path=path)
        if status_code == 415 or error_code == "UNSUPPORTED_MEDIA_TYPE":
            raise SandboxUnsupportedMediaTypeError(message, json_data.get("detail"))
        if status_code in (502, 503):
            raise SandboxUnavailableError(message, status_code)
        if status_code == 504:
            raise SandboxTimeoutError(message, operation=op)
        # Fallback
        raise SandboxFileError(message)
    
    def read(self, path: str, encoding: str = "utf-8") -> str:
        """
        Read a file from the sandbox and return its content as a string.
        
        This is a simple, non-streaming operation with a 4MB file size limit.
        For larger files, use download() instead.
        
        Args:
            path: Absolute path in the sandbox to read from (must start with /)
            encoding: Text encoding to use (default: "utf-8")
        
        Returns:
            String containing the file content
        
        Raises:
            SandboxFileNotFoundError: If the file doesn't exist
            SandboxValidationError: If path is not absolute or file exceeds 4MB
            SandboxNotFoundError: If sandbox is not found
            SandboxTimeoutError: If operation times out
            SandboxFileError: If read fails for other reasons
        
        Example:
            # Read a text file
            content = sbx.files.read("/tmp/data.txt")
            print(content)
            
            # Read with specific encoding
            content = sbx.files.read("/tmp/file.txt", encoding="latin-1")
        
        Note:
            Binary files are also returned as strings. The content is base64-decoded
            internally and then decoded using the specified encoding. For binary files,
            you may need to re-encode the string to bytes.
        """
        # Validate path is absolute
        if not path.startswith("/"):
            raise SandboxValidationError("Path must be absolute (start with /)")
        
        try:
            import base64
            
            # Prepare request
            payload = {"path": path, "encoding": encoding}
            
            response = self._sandbox._client.post(
                f"{self._sandbox._sandboxes_url}/{self._sandbox.id}/read_file",
                json=payload,
                timeout=35.0,
            )
            
            if response.status_code != 200:
                # Parse error response
                try:
                    data = response.json()
                except Exception:
                    data = {"error": response.text}
                
                error_code = (data.get("error_code") or "").upper()
                message = data.get("error") or "Read file failed"
                
                # Try to extract error_code from nested RPC error message if not at top level
                if not error_code:
                    error_code = _extract_error_code_from_rpc_message(message) or ""
                
                # Check error_code first (if present in response body)
                if error_code:
                    if error_code == "FILE_NOT_FOUND":
                        raise SandboxFileNotFoundError(message, path=path, is_local=False)
                    if error_code == "FILE_TOO_LARGE":
                        raise SandboxValidationError(message)
                    if error_code == "PERMISSION_DENIED":
                        raise SandboxPermissionDeniedError(message, path=path)
                
                # Fall back to status codes if no error_code
                if response.status_code == 404:
                    raise SandboxFileNotFoundError(message, path=path, is_local=False)
                if response.status_code == 413:
                    raise SandboxValidationError(message)
                if response.status_code == 403:
                    raise SandboxPermissionDeniedError(message, path=path)
                if response.status_code == 504:
                    raise SandboxTimeoutError(message, operation="read_file")
                if response.status_code in (502, 503):
                    raise SandboxUnavailableError(message, response.status_code)
                
                # Fallback
                raise SandboxFileError(message)
            
            data = response.json()
            
            # Decode base64 content
            content_b64 = data.get("content", "")
            if not content_b64:
                raise SandboxInvalidResponseError("Response missing 'content' field")
            
            try:
                file_bytes = base64.b64decode(content_b64)
                # Decode bytes to string using specified encoding
                return file_bytes.decode(encoding)
            except Exception as e:
                raise SandboxFileError(f"Failed to decode file content: {e}")
                
        except httpx.HTTPStatusError as e:
            self._sandbox._handle_http_error(e, "read file")
        except httpx.TimeoutException as e:
            raise SandboxTimeoutError("File read timed out", timeout_ms=35000, operation="read_file") from e
        except httpx.RequestError as e:
            raise SandboxConnectionError(f"Failed to connect to sandbox service: {e}") from e

    def write(
        self,
        path: str,
        content: str,
        overwrite: bool = False,
        encoding: str = "utf-8",
    ) -> bool:
        """
        Write a string to a file in the sandbox.
        
        This is a simple, non-streaming operation with a 4MB content size limit.
        For larger files, use upload() instead.
        
        Args:
            path: Absolute path in the sandbox where file should be written (must start with /)
            content: String content to write to the file
            overwrite: If False (default), raises error if file exists. If True, overwrites existing file.
            encoding: Text encoding to use (default: "utf-8")
        
        Returns:
            True if write was successful
        
        Raises:
            SandboxFileExistsError: If file already exists and overwrite=False
            SandboxValidationError: If path is not absolute or content exceeds 4MB
            SandboxNotFoundError: If sandbox is not found
            SandboxTimeoutError: If operation times out
            SandboxFileError: If write fails for other reasons
        
        Example:
            # Write a text file
            sbx.files.write("/tmp/output.txt", "Hello, World!")
            
            # Overwrite existing file
            sbx.files.write("/tmp/data.json", '{"key": "value"}', overwrite=True)
            
            # Write with specific encoding
            sbx.files.write("/tmp/file.txt", "Héllo", encoding="latin-1")
        
        Note:
            The content is encoded using the specified encoding and then base64-encoded
            for transmission. Binary data can be written by encoding it as a string first.
        """
        # Validate path is absolute
        if not path.startswith("/"):
            raise SandboxValidationError("Path must be absolute (start with /)")
        
        try:
            import base64
            
            # Encode string to bytes using specified encoding
            try:
                content_bytes = content.encode(encoding)
            except Exception as e:
                raise SandboxValidationError(f"Failed to encode content with {encoding}: {e}")
            
            # Check size limit (4MB)
            if len(content_bytes) > 4 * 1024 * 1024:
                raise SandboxValidationError(
                    f"Content size ({len(content_bytes)} bytes) exceeds 4MB limit. Use upload() for larger files."
                )
            
            # Base64 encode
            content_b64 = base64.b64encode(content_bytes).decode("ascii")
            
            # Prepare request
            payload = {
                "path": path,
                "content": content_b64,
                "overwrite": overwrite,
                "encoding": encoding,
            }
            
            response = self._sandbox._client.post(
                f"{self._sandbox._sandboxes_url}/{self._sandbox.id}/write_file",
                json=payload,
                timeout=35.0,
            )
            
            if response.status_code != 200:
                # Parse error response
                try:
                    data = response.json()
                except Exception:
                    data = {"error": response.text}
                
                error_code = (data.get("error_code") or "").upper()
                message = data.get("error") or "Write file failed"
                
                # Try to extract error_code from nested RPC error message if not at top level
                if not error_code:
                    error_code = _extract_error_code_from_rpc_message(message) or ""
                
                # Check error_code first (if present in response body)
                if error_code:
                    if error_code == "FILE_EXISTS":
                        raise SandboxFileExistsError(message, path=path)
                    if error_code == "CONTENT_TOO_LARGE":
                        raise SandboxValidationError(message)
                    if error_code == "PERMISSION_DENIED":
                        raise SandboxPermissionDeniedError(message, path=path)
                    if error_code == "INSUFFICIENT_STORAGE":
                        raise SandboxInsufficientStorageError(message, path=path)
                    if error_code == "FILE_NOT_FOUND":
                        raise SandboxFileNotFoundError(message, path=path)
                
                # Fall back to status codes if no error_code
                if response.status_code == 409:
                    raise SandboxFileExistsError(message, path=path)
                if response.status_code == 413:
                    raise SandboxValidationError(message)
                if response.status_code == 403:
                    raise SandboxPermissionDeniedError(message, path=path)
                if response.status_code == 507:
                    raise SandboxInsufficientStorageError(message, path=path)
                if response.status_code == 504:
                    raise SandboxTimeoutError(message, operation="write_file")
                if response.status_code in (502, 503):
                    raise SandboxUnavailableError(message, response.status_code)
                
                # Fallback
                raise SandboxFileError(message)
            
            data = response.json()
            return bool(data.get("success", False))
                
        except httpx.HTTPStatusError as e:
            self._sandbox._handle_http_error(e, "write file")
        except httpx.TimeoutException as e:
            raise SandboxTimeoutError("File write timed out", timeout_ms=35000, operation="write_file") from e
        except httpx.RequestError as e:
            raise SandboxConnectionError(f"Failed to connect to sandbox service: {e}") from e

    def upload(
        self,
        local_path: str,
        remote_path: str,
        overwrite: bool = False,
        progress: Optional[callable] = None,
        verify_checksum: bool = False,
    ) -> bool:
        """
        Upload a file from local filesystem to the sandbox.

        Args:
            local_path: Path to the local file to upload
            remote_path: Absolute path in the sandbox where file should be stored (must start with /)
            overwrite: If False (default), returns False when remote file exists. If True, overwrites existing file.
            progress: Optional callback function(bytes_sent) for upload progress tracking
            verify_checksum: If True, verifies MD5 checksum after upload completes

        Returns:
            True if upload was successful

        Raises:
            SandboxFileNotFoundError: If local file doesn't exist
            SandboxFileExistsError: If remote file already exists and overwrite=False
            SandboxValidationError: If remote_path is not absolute
            SandboxNotFoundError: If sandbox is not found
            SandboxTimeoutError: If upload times out
            SandboxFileError: If upload fails for other reasons

        Example:
            # Upload a Python script (won't overwrite if exists)
            sbx.files.upload("./script.py", "/tmp/script.py")
            
            # Upload and overwrite if exists
            sbx.files.upload("./data.json", "/home/user/data.json", overwrite=True)

        Note:
            Uploads use streaming multipart transfer and support large files. Progress can be
            tracked via the optional progress callback, and integrity can be verified via
            verify_checksum=True.
        """
        # Validate local file exists
        if not os.path.exists(local_path):
            raise SandboxFileNotFoundError(
                f"Local file not found: {local_path}", path=local_path, is_local=True
            )

        # Validate remote path is absolute
        if not remote_path.startswith("/"):
            raise SandboxValidationError("Remote path must be absolute (start with /)")

        # Multipart streaming upload
        try:
            import hashlib
            with open(local_path, "rb") as f:
                # Stream file with progress wrapper if provided
                hasher = hashlib.md5() if verify_checksum else None
                def gen():
                    sent = 0
                    chunk = f.read(1024 * 1024)
                    while chunk:
                        if hasher is not None:
                            hasher.update(chunk)
                        sent += len(chunk)
                        if progress:
                            try:
                                progress(sent)
                            except Exception:
                                pass
                        yield chunk
                        chunk = f.read(1024 * 1024)

                # Build multipart form manually to avoid loading file into memory
                boundary = "concave-boundary"
                headers = self._sandbox._client.headers.copy()
                headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"

                def multipart_stream():
                    filename = os.path.basename(local_path)
                    preamble = (
                        f"--{boundary}\r\n"
                        f"Content-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
                        f"Content-Type: application/octet-stream\r\n\r\n"
                    ).encode()
                    yield preamble
                    for chunk in gen():
                        yield chunk
                    yield f"\r\n--{boundary}--\r\n".encode()

                params = {"path": remote_path, "overwrite": str(overwrite).lower()}
                url = f"{self._sandbox._sandboxes_url}/{self._sandbox.id}/files"
                response = self._sandbox._client.build_request("PUT", url, params=params, content=multipart_stream(), headers=headers, timeout=None)
                resp = self._sandbox._client.send(response, stream=False)
                try:
                    if resp.status_code != 200:
                        # Expect JSON error envelope
                        try:
                            data = resp.json()
                        except Exception:
                            data = {"error": resp.text}
                        self._raise_file_error_from_response(resp.status_code, data, "Upload failed", remote_path, "upload")
                    data = resp.json()
                    ok = bool(data.get("success", False))
                    if not ok:
                        return False
                    # Optional checksum verification (remote vs local)
                    if verify_checksum and hasher is not None:
                        local_sum = hasher.hexdigest()
                        verify_cmd = f"md5sum {remote_path}"
                        result = self._sandbox.execute(verify_cmd)
                        if result.returncode != 0:
                            raise SandboxFileError(f"Checksum verification failed: {result.stderr}")
                        remote_sum = (result.stdout.strip().split()[0] if result.stdout else "")
                        if not remote_sum or remote_sum.lower() != local_sum.lower():
                            raise SandboxChecksumMismatchError(
                                "Checksum mismatch after upload",
                                path=remote_path,
                                expected=local_sum,
                                actual=remote_sum,
                                algorithm="md5",
                                direction="upload",
                            )
                    return True
                finally:
                    resp.close()
        except httpx.HTTPStatusError as e:
            self._sandbox._handle_http_error(e, "upload file")
        except httpx.TimeoutException as e:
            raise SandboxTimeoutError("File upload timed out", timeout_ms=None, operation="upload") from e
        except httpx.RequestError as e:
            raise SandboxConnectionError(f"Failed to connect to sandbox service: {e}") from e

    def download(
        self,
        remote_path: str,
        local_path: str,
        overwrite: bool = False,
        progress: Optional[callable] = None,
        verify_checksum: bool = False,
    ) -> bool:
        """
        Download a file from the sandbox to local filesystem.

        Args:
            remote_path: Absolute path in the sandbox to download from (must start with /)
            local_path: Path on local filesystem where file should be saved
            overwrite: If False (default), returns False when local file exists. If True, overwrites existing file.
            progress: Optional callback function(bytes_downloaded) for download progress tracking
            verify_checksum: If True, calculates remote MD5 first, then verifies downloaded file matches

        Returns:
            True if download was successful

        Raises:
            SandboxFileNotFoundError: If remote file doesn't exist
            SandboxFileExistsError: If local file already exists and overwrite=False
            SandboxValidationError: If remote_path is not absolute
            SandboxNotFoundError: If sandbox is not found
            SandboxTimeoutError: If download times out
            SandboxFileError: If download fails for other reasons

        Example:
            # Download a result file (won't overwrite if exists)
            sbx.files.download("/tmp/output.txt", "./results/output.txt")
            
            # Download and overwrite if exists
            sbx.files.download("/home/user/data.csv", "./data.csv", overwrite=True)
            
            # Download with checksum verification
            sbx.files.download("/tmp/data.bin", "./data.bin", verify_checksum=True)

        Note:
            When verify_checksum=True, the remote file's MD5 is calculated first via execute(),
            then the file is downloaded and verified locally against that checksum. If the
            checksums do not match, a SandboxChecksumMismatchError is raised.
        """
        # Validate remote path is absolute
        if not remote_path.startswith("/"):
            raise SandboxValidationError("Remote path must be absolute (start with /)")

        # Check if local file exists and overwrite is disabled
        if os.path.exists(local_path) and not overwrite:
            raise SandboxFileExistsError(
                f"Local file already exists: {local_path}", path=local_path
            )

        # Create parent directory if needed
        local_dir = os.path.dirname(local_path)
        if local_dir:
            os.makedirs(local_dir, exist_ok=True)

        # If checksum verification requested, calculate remote checksum FIRST
        expected_checksum = None
        if verify_checksum:
            verify_cmd = f"md5sum {remote_path}"
            result = self._sandbox.execute(verify_cmd)
            if result.returncode != 0:
                # md5sum failed - likely file doesn't exist or permission denied
                raise SandboxFileError(f"Failed to calculate remote checksum: {result.stderr}")
            expected_checksum = result.stdout.strip().split()[0] if result.stdout else ""
            if not expected_checksum:
                raise SandboxFileError("Failed to parse remote checksum from md5sum output")

        # Stream download
        try:
            import hashlib
            with self._sandbox._client.stream(
                "GET",
                f"{self._sandbox._sandboxes_url}/{self._sandbox.id}/files",
                params={"path": remote_path},
                timeout=None,
            ) as resp:
                if resp.status_code != 200:
                    # Read the response body from the streaming response first
                    raw = resp.read()
                    # Try to parse JSON; otherwise, include plain text error
                    try:
                        data = resp.json()
                    except Exception:
                        try:
                            text = raw.decode("utf-8", errors="replace")
                        except Exception:
                            text = ""
                        data = {"error": text}
                    self._raise_file_error_from_response(resp.status_code, data, "Download failed", remote_path, "download")

                total = 0
                hasher = hashlib.md5() if verify_checksum else None
                os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
                with open(local_path, "wb") as f:
                    for chunk in resp.iter_bytes(1024 * 1024):
                        if chunk:
                            f.write(chunk)
                            total += len(chunk)
                            if hasher is not None:
                                hasher.update(chunk)
                            if progress:
                                try:
                                    progress(total)
                                except Exception:
                                    pass
                
                # Verify checksum if requested
                if verify_checksum and hasher is not None:
                    actual_sum = hasher.hexdigest()
                    if actual_sum.lower() != expected_checksum.lower():
                        raise SandboxChecksumMismatchError(
                            "Checksum mismatch after download",
                            path=remote_path,
                            expected=expected_checksum,
                            actual=actual_sum,
                            algorithm="md5",
                            direction="download",
                        )
                return True
        except httpx.HTTPStatusError as e:
            self._sandbox._handle_http_error(e, "download file")
        except httpx.TimeoutException as e:
            raise SandboxTimeoutError("File download timed out", timeout_ms=None, operation="download") from e
        except httpx.RequestError as e:
            raise SandboxConnectionError(f"Failed to connect to sandbox service: {e}") from e


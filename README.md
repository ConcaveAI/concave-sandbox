# Concave AI - Python SDK

**Concave AI** is a Python SDK for the Concave sandbox service, providing isolated code execution environments backed by Firecracker microVMs. Execute untrusted code safely with strong isolation, fast performance, and a simple, intuitive API.

## Features

- **Secure Isolation**: Each sandbox runs in its own Firecracker microVM for strong security
- **Fast Performance**: Lightweight VMs boot in milliseconds
- **Python-First**: Native support for executing Python code with tmpfs-backed isolation
- **Shell Access**: Execute arbitrary shell commands in the sandbox
- **Simple API**: Clean, intuitive interface with context manager support
- **Production Ready**: Comprehensive error handling and type hints

## Installation

```bash
pip install concave-sandbox
```

## Quick Start

### Get Your API Key

Sign up at [concave.ai](https://concave.ai) to get your API key.

### Basic Usage

```python
from concave import Sandbox

# Set your API key (or use CONCAVE_API_KEY environment variable)
sbx = Sandbox.create(name="my-sandbox", api_key="cnc_your_api_key_here")

# Execute shell commands
result = sbx.execute("uname -a")
print(result.stdout)  # Linux ...

# Run Python code
result = sbx.run("print('Hello from Concave!')")
print(result.stdout)  # Hello from Concave!

# Clean up
sbx.delete()
```

### Context Manager (Recommended)

The SDK supports Python's context manager protocol for automatic cleanup:

```python
from concave import sandbox

with sandbox(name="my-sandbox", api_key="cnc_your_api_key_here") as sbx:
    # Execute commands
    result = sbx.run("""
import requests
response = requests.get('https://api.github.com')
print(response.status_code)
    """)
    print(result.stdout)  # 200
# Sandbox is automatically deleted after the with block
```

## API Reference

### Sandbox

#### `Sandbox.create(name, base_url=None, api_key=None)`

Create a new sandbox instance.

**Parameters:**
- `name` (str): Human-readable name for the sandbox
- `base_url` (str, optional): Base URL of the sandbox service. Defaults to `SANDBOX_BASE_URL` env var or `https://api.concave.dev`
- `api_key` (str, optional): API key for authentication. Defaults to `CONCAVE_API_KEY` env var

**Returns:** `Sandbox` instance

**Raises:**
- `SandboxCreationError`: If sandbox creation fails
- `ValueError`: If api_key is not provided and `CONCAVE_API_KEY` is not set

#### `sandbox.execute(command, timeout=None)`

Execute a shell command in the sandbox.

**Parameters:**
- `command` (str): Shell command to execute
- `timeout` (int, optional): Timeout in milliseconds (default: 10000ms)

**Returns:** `ExecuteResult` with `stdout`, `stderr`, `returncode`, and `command` fields

**Raises:**
- `SandboxExecutionError`: If execution fails
- `SandboxNotFoundError`: If sandbox doesn't exist
- `SandboxValidationError`: If command is empty

#### `sandbox.run(code, timeout=None)`

Run Python code in the sandbox.

**Parameters:**
- `code` (str): Python code to execute
- `timeout` (int, optional): Timeout in milliseconds (default: 10000ms)

**Returns:** `RunResult` with `stdout`, `stderr`, `returncode`, and `code` fields

**Raises:**
- `SandboxExecutionError`: If execution fails
- `SandboxNotFoundError`: If sandbox doesn't exist
- `SandboxValidationError`: If code is empty

#### `sandbox.status()`

Get current sandbox status.

**Returns:** Dictionary with sandbox metadata including `id`, `ip`, `pid`, `state`, `exec_count`, etc.

#### `sandbox.delete()`

Delete the sandbox and free resources.

**Returns:** `bool` - True if deletion was successful

### Context Manager

#### `sandbox(name="sandbox", base_url=None, api_key=None)`

Context manager that automatically creates and cleans up a sandbox.

**Example:**
```python
with sandbox(name="temp") as sbx:
    result = sbx.run("print('temporary sandbox')")
    print(result.stdout)
```

## Error Handling

The SDK provides a comprehensive exception hierarchy:

```python
from concave import (
    SandboxError,              # Base exception
    SandboxClientError,        # Client errors (4xx)
    SandboxServerError,        # Server errors (5xx)
    SandboxNetworkError,       # Network errors
    SandboxAuthenticationError,
    SandboxNotFoundError,
    SandboxRateLimitError,
    SandboxValidationError,
    SandboxTimeoutError,
)

try:
    with sandbox(api_key="invalid") as sbx:
        result = sbx.run("print('test')")
except SandboxAuthenticationError:
    print("Invalid API key")
except SandboxRateLimitError as e:
    print(f"Rate limited: {e.limit}")
except SandboxTimeoutError as e:
    print(f"Timed out after {e.timeout_ms}ms")
```

## Advanced Examples

### Custom Timeout

```python
with sandbox() as sbx:
    # 30 second timeout
    result = sbx.run("import time; time.sleep(25)", timeout=30000)
    print(f"Completed in time: {result.returncode == 0}")
```

### Installing Packages

```python
with sandbox() as sbx:
    # Install a package
    sbx.execute("pip install numpy")
    
    # Use the package
    result = sbx.run("""
import numpy as np
arr = np.array([1, 2, 3, 4, 5])
print(f'Mean: {arr.mean()}')
    """)
    print(result.stdout)  # Mean: 3.0
```

### Error Checking

```python
with sandbox() as sbx:
    result = sbx.run("""
import sys
print("Output to stdout")
print("Output to stderr", file=sys.stderr)
sys.exit(1)  # Non-zero exit code
    """)
    
    print(f"Exit code: {result.returncode}")  # 1
    print(f"Stdout: {result.stdout}")
    print(f"Stderr: {result.stderr}")
```

## Environment Variables

- `CONCAVE_API_KEY`: Your Concave API key
- `SANDBOX_BASE_URL`: Base URL for the sandbox service (default: `https://api.concave.dev`)

## Requirements

- Python 3.8+
- httpx >= 0.25.0

## License

MIT License - see LICENSE file for details

## Support

- Documentation: [docs.concave.ai](https://docs.concave.ai)
- Issues: [GitHub Issues](https://github.com/ConcaveAI/concave-sandbox/issues)
- Email: support@concave.ai

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.


<p align="center">
  <img src="https://raw.githubusercontent.com/ConcaveAI/concave-sandbox/refs/heads/main/assets/cover.png" alt="Concave Sandbox Banner" width="100%">
</p>

## What's this?

This is a Python SDK for creating and managing sandboxes. These sandboxes run at scale on our infrastructure, while you can focus on using them to do anything you want.

## Well, what can I do with it?

Run untrusted AI generated code, power deep research systems, environment for coding agents, train RL agents, malware analysis, or build interactive compute experiences—all in secure, high-performance sandboxes.

## Features

- **Secure Isolation**: Complete VM-level isolation using Firecracker microVMs—every sandbox runs in its own kernel (unlike Docker containers that share the host kernel)
- **Python Execution**: Run Python code securely in isolated sandboxes
- **Blazing Fast**: Full VM boot up in under 200ms
- **Simple API**: Clean, intuitive interface with easy-to-use client SDKs
- **Production Ready**: Comprehensive error handling and type hints

## Installation

```bash
pip install concave-sandbox
```

## Quick Start

### Configuration

Set your API key as an environment variable:

```bash
export CONCAVE_SANDBOX_API_KEY="your_api_key_here"
```

Sign up at [concave.ai](https://concave.ai) to get your API key.

### Run Python Code

Execute Python code securely in isolated sandboxes:

```python
from concave import sandbox

with sandbox(name="python-sandbox") as sbx:
    result = sbx.run("print(668.5 * 2)")
    print(result.stdout) 
    
# Output: 1337.0
```

### Manual Cleanup

If you prefer to manage the sandbox lifecycle yourself:

```python
from concave import Sandbox

sbx = Sandbox.create(name="my-sandbox")

# Execute shell commands
result = sbx.execute("uname -a")
print(result.stdout)  # Linux ...

# Run Python code
result = sbx.run("print('Hello from Python!')")
print(result.stdout)  # Hello from Python!

# Clean up
sbx.delete()
```

## Documentation

For complete API reference, advanced examples, error handling, and best practices, visit [docs.concave.ai](https://docs.concave.ai).


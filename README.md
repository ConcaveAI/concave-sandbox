<p align="center">
  <img src="assets/cover.png" alt="Concave Sandbox Banner" width="100%">
</p>

## What's this?

Run code safely in isolated cloud containers. Run untrusted code, train RL agents, power autonomous research systems, or build interactive compute experiences—all in secure, high-performance sandboxes.

## Features

- **Secure Isolation**: Complete VM-level isolation using Firecracker microVMs—every sandbox runs in its own kernel (unlike Docker containers that share the host kernel)
- **Multi-Language**: Run Python and Node.js code with a unified API
- **Blazing Fast**: Full VM boot up in under 200ms
- **Simple API**: Clean, intuitive interface with easy-to-use client SDKs
- **Production Ready**: Comprehensive error handling and type hints

## Installation

```bash
pip install concave-sandbox
```

## Quick Start

### Get Your API Key

Sign up at [concave.ai](https://concave.ai) to get your API key.

### Simple Example

```python
from concave import sandbox

with sandbox(name="my-sandbox", api_key="cnc_your_api_key_here") as sbx:
    result = sbx.run("print('Hello from Concave!')")
    print(result.stdout)  # Hello from Concave!

# Sandbox is automatically deleted when done
```

### Multi-Language Support

Run code in Python or Node.js:

```python
from concave import sandbox

with sandbox(name="multi-lang", api_key="cnc_your_api_key_here") as sbx:
    # Run Python code (language="python" is default)
    result = sbx.run("print('Hello from Python!')")
    print(result.stdout)  # Hello from Python!
    
    # Run Node.js code
    result = sbx.run("console.log('Hello from Node.js!')", language="nodejs")
    print(result.stdout)  # Hello from Node.js!
```

### Manual Cleanup

If you prefer to manage the sandbox lifecycle yourself:

```python
from concave import Sandbox

sbx = Sandbox.create(name="my-sandbox", api_key="cnc_your_api_key_here")

# Execute shell commands
result = sbx.execute("uname -a")
print(result.stdout)  # Linux ...

# Run Python code (language="python" is default)
result = sbx.run("print('Hello from Python!')")
print(result.stdout)  # Hello from Python!

# Run Node.js code
result = sbx.run("console.log(process.version)", language="nodejs")
print(result.stdout)  # v18.20.4

# Clean up
sbx.delete()
```

## Documentation

For complete API reference, advanced examples, error handling, and best practices, visit [docs.concave.ai](https://docs.concave.ai).


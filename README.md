<p align="center">
  <img src="https://raw.githubusercontent.com/ConcaveAI/concave-sandbox/refs/heads/main/assets/cover.png" alt="Concave Sandbox Banner" width="100%">
</p>

## What's this?

This is a Python SDK for creating and managing sandboxes. These sandboxes run at scale on our infrastructure, while you can focus on using them to do anything you want.

## Well, what can I do with it?

Run untrusted AI generated code, power deep research systems, environment for coding agents, train RL agents, malware analysis, or build interactive compute experiences—all in secure, high-performance sandboxes.

## Features

- **Secure Isolation**: Complete VM-level isolation using Firecracker microVMs—every sandbox runs in its own kernel (unlike Docker containers that share the host kernel)
- **Code Execution**: Run Python and JavaScript code securely in isolated sandboxes
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

### Run Code

Execute Python or JavaScript code securely in isolated sandboxes:

```python
from concave import sandbox

with sandbox() as sbx:
    result = sbx.run("print(668.5 * 2)")
    print(result.stdout) 
    
# Output: 1337.0
```

### Manual Cleanup

If you prefer to manage the sandbox lifecycle yourself:

```python
from concave import Sandbox

sbx = Sandbox.create()

# Execute shell commands
result = sbx.execute("uname -a")
print(result.stdout)  # Linux ...

# Run Python code
result = sbx.run("print('Hello from Python!')")
print(result.stdout)  # Hello from Python!

# Run JavaScript code
result = sbx.run("console.log('Hello from Node.js!')", language="javascript")
print(result.stdout)  # Hello from Node.js!

# Clean up
sbx.delete()
```

### Working with Files, Network, and Monitoring

The SDK organizes operations into intuitive namespaces:

```python
from concave import Sandbox

sbx = Sandbox.create()

# Files namespace - read, write, upload, download
sbx.files.write("/tmp/data.txt", "Hello, Concave!")
content = sbx.files.read("/tmp/data.txt")
print(content)  # Hello, Concave!

# Upload local file to sandbox
sbx.files.upload("./script.py", "/tmp/script.py")

# Download from sandbox to local
sbx.files.download("/tmp/output.txt", "./results/output.txt")

# Network namespace - publish ports publicly
sbx.execute("python3 -m http.server 8000 &")
url = sbx.network.publish(8000)
print(f"Access at: https://{url}")  # Access at: https://xyz123.concave.run

# Monitor namespace - health and status
if sbx.monitor.ping():
    print("Sandbox is alive!")
    
uptime = sbx.monitor.uptime()
print(f"Uptime: {uptime} seconds")

status = sbx.monitor.status()
print(f"State: {status['state']}, Executions: {status['exec_count']}")

# Clean up
sbx.delete()
```

### Listing Sandboxes

List your active sandboxes with built-in pagination:

```python
from concave import Sandbox

# List first 10 sandboxes (default)
sandboxes = Sandbox.list()
for sbx in sandboxes:
    print(f"Sandbox {sbx.id}")

# Check for more pages
if sandboxes.has_more:
    next_page = Sandbox.list(cursor=sandboxes.next_cursor)
    
# Custom limit (max 100)
sandboxes = Sandbox.list(limit=50)

# Filter sandboxes
active = Sandbox.list(internet_access=True, min_exec_count=5)
```

## Documentation

For complete API reference, advanced examples, error handling, and best practices, visit [docs.concave.ai](https://docs.concave.ai).


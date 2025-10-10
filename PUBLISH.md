# Publishing concave-ai to PyPI

This guide walks you through publishing the `concave-ai` package to PyPI.

## Prerequisites

1. **PyPI Account**: Create accounts on both:
   - [PyPI](https://pypi.org/account/register/) (production)
   - [TestPyPI](https://test.pypi.org/account/register/) (testing)

2. **Install build tools**:
   ```bash
   pip install --upgrade build twine
   ```

## Step 1: Update Version

Before publishing, update the version in `pyproject.toml`:

```toml
[project]
version = "0.1.0"  # Update this for new releases
```

Follow [Semantic Versioning](https://semver.org/):
- `0.1.0` → `0.1.1` for bug fixes
- `0.1.0` → `0.2.0` for new features
- `0.1.0` → `1.0.0` for breaking changes

## Step 2: Clean Previous Builds

```bash
cd module/
rm -rf build/ dist/ *.egg-info/
```

## Step 3: Build the Package

```bash
python -m build
```

This creates:
- `dist/concave_ai-0.1.0-py3-none-any.whl` (wheel file)
- `dist/concave-ai-0.1.0.tar.gz` (source distribution)

## Step 4: Test on TestPyPI (Recommended)

First, test your package on TestPyPI:

```bash
# Upload to TestPyPI
python -m twine upload --repository testpypi dist/*

# You'll be prompted for:
# Username: __token__
# Password: <your TestPyPI API token>
```

Then test installation:

```bash
pip install --index-url https://test.pypi.org/simple/ concave-ai
```

## Step 5: Publish to PyPI

Once you've verified everything works:

```bash
python -m twine upload dist/*

# You'll be prompted for:
# Username: __token__
# Password: <your PyPI API token>
```

## Step 6: Verify Installation

```bash
pip install concave-ai
```

Test it works:

```python
from concave import Sandbox
print(Sandbox.__doc__)
```

## Using API Tokens (Recommended)

Instead of entering credentials each time, use API tokens:

1. **Get API tokens**:
   - PyPI: https://pypi.org/manage/account/token/
   - TestPyPI: https://test.pypi.org/manage/account/token/

2. **Create `~/.pypirc`**:
   ```ini
   [distutils]
   index-servers =
       pypi
       testpypi

   [pypi]
   username = __token__
   password = pypi-AgEIcHlwaS5vcmc...

   [testpypi]
   username = __token__
   password = pypi-AgENdGVzdC5weXBp...
   ```

3. **Set permissions**:
   ```bash
   chmod 600 ~/.pypirc
   ```

Now you can upload without entering credentials:

```bash
python -m twine upload dist/*
```

## Quick Publish Script

For convenience, you can use this one-liner:

```bash
# Clean, build, and publish to PyPI
rm -rf build/ dist/ *.egg-info/ && python -m build && python -m twine upload dist/*
```

Or for TestPyPI:

```bash
# Clean, build, and publish to TestPyPI
rm -rf build/ dist/ *.egg-info/ && python -m build && python -m twine upload --repository testpypi dist/*
```

## Troubleshooting

### "File already exists"
- You can't overwrite existing versions on PyPI
- Increment the version number in `pyproject.toml`

### "Invalid distribution"
- Make sure all files are properly formatted
- Check that `pyproject.toml` has no syntax errors
- Verify `README.md` renders correctly (check on GitHub)

### "Package name already taken"
- If `concave-ai` is taken, you'll need to choose a different name
- Update the `name` field in `pyproject.toml`

## Updating the Package

When making updates:

1. Update version in `pyproject.toml`
2. Update `__version__` in `__init__.py` to match
3. Clean, build, and publish:
   ```bash
   rm -rf build/ dist/ *.egg-info/
   python -m build
   python -m twine upload dist/*
   ```

## Best Practices

- Always test on TestPyPI first
- Use semantic versioning
- Keep `__version__` in sync with `pyproject.toml`
- Document changes in a CHANGELOG.md
- Tag releases in git: `git tag v0.1.0 && git push --tags`
- Never delete published versions (publish a new version instead)

## Resources

- [Python Packaging User Guide](https://packaging.python.org/)
- [PyPI Help](https://pypi.org/help/)
- [Semantic Versioning](https://semver.org/)


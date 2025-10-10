.PHONY: clean build test-publish publish install-dev lint format check help

help:
	@echo "Concave AI - PyPI Publishing Commands"
	@echo ""
	@echo "Available targets:"
	@echo "  clean         - Remove build artifacts"
	@echo "  build         - Build the package"
	@echo "  test-publish  - Publish to TestPyPI"
	@echo "  publish       - Publish to PyPI (production)"
	@echo "  install-dev   - Install in development mode"
	@echo "  lint          - Run ruff linter"
	@echo "  format        - Format code with ruff"
	@echo "  check         - Run linter and check formatting"
	@echo "  help          - Show this help message"

clean:
	@echo "Cleaning build artifacts..."
	rm -rf build/ dist/ *.egg-info/ __pycache__/
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	@echo "✓ Clean complete"

build: clean
	@echo "Building package..."
	python -m build
	@echo "✓ Build complete"
	@echo ""
	@echo "Generated files:"
	@ls -lh dist/

test-publish: build
	@echo "Publishing to TestPyPI..."
	python -m twine upload --repository testpypi dist/*
	@echo ""
	@echo "✓ Published to TestPyPI"
	@echo "Test installation with:"
	@echo "  pip install --index-url https://test.pypi.org/simple/ concave-sandbox"

publish: build
	@echo "Publishing to PyPI..."
	@read -p "Are you sure you want to publish to PyPI? [y/N] " -n 1 -r; \
	echo; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		python -m twine upload dist/*; \
		echo ""; \
		echo "✓ Published to PyPI"; \
		echo "Install with: pip install concave-sandbox"; \
	else \
		echo "Aborted."; \
	fi

install-dev:
	@echo "Installing in development mode..."
	pip install -e .
	pip install -r requirements-dev.txt
	@echo "✓ Development installation complete"

lint:
	@echo "Running ruff linter..."
	ruff check .
	@echo "✓ Linting complete"

format:
	@echo "Formatting code with ruff..."
	ruff format .
	@echo "✓ Formatting complete"

check: lint
	@echo "Checking code formatting..."
	ruff format --check .
	@echo "✓ All checks passed"


.PHONY: help install install-mlx install-tts lint check test test-slow coverage build frontend frontend-dev docs docs-serve docs-build clean

# ==============================================================================
# Venv
# ==============================================================================

UV := $(shell command -v uv 2> /dev/null)
VENV_DIR ?= .venv
PYTHON := $(VENV_DIR)/bin/python

# ==============================================================================
# Targets
# ==============================================================================

help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  install     Install dependencies"
	@echo "  install-mlx Install deps + MLX runtimes (Apple Silicon only)"
	@echo "  install-tts Install deps + Kokoro TTS (multi-voice; macOS + Linux)"
	@echo "  lint        Format + autofix, then type-check (mutates files)"
	@echo "  check       CI gate: verify formatting/lint/types/tests (no changes)"
	@echo "  test        Run tests"
	@echo "  coverage    Run tests with coverage reporting"
	@echo "  build       Build wheel + sdist"
	@echo "  frontend    Build the browser UI (npm install + build → frontend/dist)"
	@echo "  docs        Serve the docs locally with live reload"
	@echo "  docs-build  Build the docs site"
	@echo "  clean       Clean up temporary files"

install:
	@echo ">>> Installing dependencies"
	@$(UV) sync

install-mlx:
	@echo ">>> Installing dependencies + MLX runtimes (Apple Silicon only)"
	@$(UV) sync --extra mlx

install-tts:
	@echo ">>> Installing dependencies + Kokoro TTS (multi-voice; macOS + Linux)"
	@$(UV) sync --extra tts

lint:
	@echo ">>> Running linter"
	@$(UV) run ruff format .
	@$(UV) run ruff check . --fix
	@echo ">>> Running type checker"
	@$(UV) run mypy --explicit-package-bases src tests
	@$(UV) run pyright

check:
	@echo ">>> Checking formatting and lint (no changes)"
	@$(UV) run ruff format --check .
	@$(UV) run ruff check .
	@echo ">>> Running type checker"
	@$(UV) run mypy --explicit-package-bases src tests
	@$(UV) run pyright
	@echo ">>> Running tests (excluding slow)"
	@$(UV) run pytest -q -m "not slow"

test:
	@echo ">>> Running tests (excluding slow)"
	@$(UV) run pytest -q -m "not slow"

test-slow:
	@echo ">>> Running slow live e2e (needs llama-server + a library model)"
	@$(UV) run pytest -q -m slow -s

coverage:
	@echo ">>> Running tests with coverage (excluding slow)"
	@$(UV) run coverage run -m pytest -q -m "not slow"
	@$(UV) run coverage report
	@$(UV) run coverage xml

build:
	@echo ">>> Building wheel + sdist"
	@$(UV) build

frontend:
	@echo ">>> Building the browser UI → frontend/dist"
	@cd frontend && npm install && npm run build

frontend-dev:
	@echo ">>> Vite dev server (proxies /api + /v1 to KODO_DEV_API or :8000)"
	@echo ">>> Run 'kodo serve --port 8000' alongside for the backend"
	@cd frontend && npm run dev

docs: docs-serve

docs-serve:
	@echo ">>> Serving docs at http://127.0.0.1:8001"
	@NO_MKDOCS_2_WARNING=1 $(UV) run mkdocs serve -a 127.0.0.1:8001

docs-build:
	@echo ">>> Building docs site"
	@NO_MKDOCS_2_WARNING=1 $(UV) run mkdocs build

clean:
	@echo ">>> Cleaning up"
	@find . -type f -name "*.pyc" -delete
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .coverage htmlcov coverage.xml
	@rm -rf .pyright site
	@rm -rf dist build *.egg-info

# ==============================================================================
# Default
# ==============================================================================

.DEFAULT_GOAL := help

.PHONY: help install lint check test coverage run dev serve build docs docs-serve docs-build clean

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
	@echo "  lint        Format + autofix, then type-check (mutates files)"
	@echo "  check       CI gate: verify formatting/lint/types/tests (no changes)"
	@echo "  test        Run tests"
	@echo "  coverage    Run tests with coverage reporting"
	@echo "  run         Start backend + UI (use MODEL=<name> to lock to one model)"
	@echo "  dev         Run the API with auto-reload"
	@echo "  serve       Run the API (no reload)"
	@echo "  build       Build wheel + sdist"
	@echo "  docs        Serve the docs locally with live reload"
	@echo "  docs-build  Build the docs site"
	@echo "  clean       Clean up temporary files"

install:
	@echo ">>> Installing dependencies"
	@$(UV) sync

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
	@echo ">>> Running tests"
	@$(UV) run pytest -q

test:
	@echo ">>> Running tests"
	@$(UV) run pytest -q

coverage:
	@echo ">>> Running tests with coverage"
	@$(UV) run coverage run -m pytest -q
	@$(UV) run coverage report
	@$(UV) run coverage xml

# make run               → backend + UI, free model switching
# make run MODEL=<name>  → backend + UI locked to one model (extension backend)
run:
	@echo ">>> Starting backend + UI$(if $(MODEL), (locked: $(MODEL)),)"
	@$(UV) run local-llm serve --ui $(if $(MODEL),--model $(MODEL),)

dev:
	@echo ">>> Starting dev server (fastapi dev, auto-reload)"
	@$(UV) run fastapi dev src/kodo/app.py

serve:
	@echo ">>> Starting server"
	@$(UV) run local-llm serve

build:
	@echo ">>> Building wheel + sdist"
	@$(UV) build

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

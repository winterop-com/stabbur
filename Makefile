.PHONY: help install install-mlx install-web lint check test test-slow coverage build frontend frontend-dev extension extension-e2e extension-e2e-live extension-prompts docs docs-serve docs-build clean

# ==============================================================================
# Venv
# ==============================================================================

UV := $(shell command -v uv 2> /dev/null)
VENV_DIR ?= .venv
PYTHON := $(VENV_DIR)/bin/python

# Type-check the app + the remaining workspace member (packages/heim-sandbox). Its src/
# and tests/ go on MYPYPATH so its flat module resolves to a clean, hyphen-free name
# (the packages/ dir name contains '-'). Globs expand at recipe time. The bundled MCP
# servers are now vendored under src/heim/mcp_servers/** and covered by the `src` root.
MYPY = MYPYPATH="$$(echo src packages/*/src packages/*/tests | tr ' ' ':')" $(UV) run mypy \
	--explicit-package-bases src tests packages/*/src packages/*/tests

# ==============================================================================
# Targets
# ==============================================================================

help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  install     Install dependencies (incl. the benchmark/eval harness)"
	@echo "  install-mlx Install deps + MLX runtimes (Apple Silicon only)"
	@echo "  install-web Install deps + web reader (Playwright) + Chromium browser"
	@echo "  lint        Format + autofix, then type-check (mutates files)"
	@echo "  check       CI gate: verify formatting/lint/types/tests (no changes)"
	@echo "  test        Run tests"
	@echo "  coverage    Run tests with coverage reporting"
	@echo "  build       Build wheel + sdist"
	@echo "  frontend    Build the browser UI (bun install + build → frontend/dist)"
	@echo "  extension   Build the Chrome MV3 side-panel extension (bun install + build)"
	@echo "  extension-e2e       Run the extension E2E mock tier (fast, hermetic)"
	@echo "  extension-e2e-live  Run the extension E2E live tier (real heim + DHIS2 demo)"
	@echo "  extension-prompts   Verify the side-panel prompt catalog vs gemma-4-12B + regen the doc"
	@echo "  docs        Serve the docs locally with live reload"
	@echo "  docs-build  Build the docs site"
	@echo "  clean       Clean up temporary files"

# The `benchmark` extra rides along in every install target: it's a dev/eval tier (its
# code is vendored at src/heim/benchmark and its tests live under tests/benchmark, collected
# by `make check`). The extra itself is now empty, but naming it keeps the invocation valid
# and self-documenting; the dev group (which carries heim-sandbox, the executor) syncs too.
install:
	@echo ">>> Installing dependencies (incl. benchmark/eval harness)"
	@$(UV) sync --extra benchmark

install-mlx:
	@echo ">>> Installing dependencies + MLX runtimes (Apple Silicon only)"
	@$(UV) sync --extra benchmark --extra mlx

install-web:
	@echo ">>> Installing dependencies + web reader (Playwright) + Chromium"
	@$(UV) sync --extra benchmark --extra web
	@$(UV) run playwright install chromium

# The SPA's half of the gate. `oxlint` (frontend/.oxlintrc.json) covers the JS/TS; it cannot see
# inside a className, so the UI conventions get their own check — see scripts/check_ui_classes.py
# and docs/ui-conventions.md. Both are read-only, so `lint` and `check` share them.
#
# `bun install --frozen-lockfile` rides along for the same reason `uv run` auto-syncs: the linter
# has to be present for the gate to mean anything, and a frozen install touches no tracked file.
FRONTEND_LINT = cd frontend && bun install --frozen-lockfile >/dev/null && bun run lint

lint:
	@echo ">>> Running linter"
	@$(UV) run ruff format .
	@$(UV) run ruff check . --fix
	@echo ">>> Running type checker"
	@$(MYPY)
	@$(UV) run pyright
	@echo ">>> Linting the browser UI (oxlint + UI conventions)"
	@$(FRONTEND_LINT)
	@$(UV) run python scripts/check_ui_classes.py

check:
	@echo ">>> Checking formatting and lint (no changes)"
	@$(UV) run ruff format --check .
	@$(UV) run ruff check .
	@echo ">>> Running type checker"
	@$(MYPY)
	@$(UV) run pyright
	@echo ">>> Linting the browser UI (oxlint + UI conventions)"
	@$(FRONTEND_LINT)
	@$(UV) run python scripts/check_ui_classes.py
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
	@cd frontend && bun install && bun run build

frontend-dev:
	@echo ">>> Vite dev server (proxies /api + /v1 to HEIM_DEV_API or :8000)"
	@echo ">>> Run 'heim serve --port 8000' alongside for the backend"
	@cd frontend && bun run dev

extension:
	@echo ">>> Building the Chrome MV3 side-panel extension → extension/.output/chrome-mv3"
	@cd extension && bun install && bun run build

extension-e2e:
	@echo ">>> Extension E2E: mock tier (builds + runs Playwright, headless)"
	@cd extension && bun install && bun run e2e

# Long model runs must survive an idle machine: macOS Maintenance Sleep suspends the
# runtime mid-generation and poisons results with bogus timeouts. No-op off macOS.
CAFFEINATE := $(shell command -v caffeinate >/dev/null 2>&1 && echo "caffeinate -is")

extension-e2e-live:
	@echo ">>> Extension E2E: live tier (real heim serve + DHIS2 play demo; ~5-15 min)"
	@cd extension && bun install && $(CAFFEINATE) bun run e2e:live

extension-prompts:
	@echo ">>> Extension prompt catalog: capture sites + verify against gemma-4-12B, then regenerate the doc"
	@cd extension && bun install && $(CAFFEINATE) bun run prompts && bun run prompts:doc

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

"""Shared pytest fixtures / test-session setup.

Neutralize color-forcing env vars at import time — before any test module (and
thus ``stabbur.cli``'s module-level Rich ``Console()``) is imported. CLI tests
assert on substrings of Rich output; a ``FORCE_COLOR`` / ``CLICOLOR_FORCE`` in
the environment (some terminals and CI wrappers set it) makes Rich inject ANSI
escapes mid-string and breaks those assertions. Rich fixes the color system when
the Console is constructed, so this must run before that import, not in a fixture.
"""

import os
import tempfile
from pathlib import Path

import pytest

for _var in ("FORCE_COLOR", "CLICOLOR_FORCE"):
    os.environ.pop(_var, None)
os.environ["NO_COLOR"] = "1"

# Pin a wide console so Rich doesn't wrap CLI output mid-string. Without a TTY (CI, pytest
# capture) Rich falls back to 80 columns and honors ``COLUMNS`` — a narrow width breaks
# substring assertions when a hint line is long enough to wrap between two asserted words
# (e.g. a copy-paste ``ollama run`` command straddling the fold). Set before ``stabbur.cli``'s
# module-level Console is constructed, same as the color vars above.
os.environ["COLUMNS"] = "200"

# Point the suite at an empty, throwaway library so it's hermetic. Without this the
# tests inherit whatever ``STABBUR_LIBRARY_ROOT`` the developer's ``.env`` supplies —
# which passes locally but fails on a clean checkout (CI) with ``LibraryNotConfigured``,
# and silently scans the real drive locally. An env var overrides ``.env``, so this wins
# everywhere. Tests that need the *unconfigured* state ``monkeypatch.delenv`` it explicitly.
os.environ.setdefault("STABBUR_LIBRARY_ROOT", tempfile.mkdtemp(prefix="stabbur-test-library-"))

# Point the machine config (stabbur.userconfig, read as the lowest-priority Settings source) at a
# throwaway XDG dir so the suite never reads the developer's real ~/.config/stabbur/config.toml —
# which would leak a default_model / library_root into tests and make them non-hermetic. Tests
# that exercise the machine config set XDG_CONFIG_HOME to their own tmp_path.
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="stabbur-test-config-")

# Point the ephemeral runtime state (pidfiles + logs, and the sibling serve registry) at a
# throwaway dir so the suite never writes into the real ``$XDG_RUNTIME_DIR/stabbur/runtimes``
# (or ``~/.cache/stabbur/runtimes``). Tests that spawn a runtime create and delete entries
# there, which both mutates machine state a test run has no business touching and races the
# orphan sweep — a rmtree'd state dir under a concurrent ``_write_meta`` surfaced as an
# unhandled ``FileNotFoundError`` on ``.../runtimes/<id>/meta.json`` from a worker thread.
#
# Via ``STABBUR_RUNTIME_STATE_DIR`` rather than ``XDG_CACHE_HOME``: ``Settings`` evaluates the
# XDG-derived default at *import* time (``stabbur.config._default_runtime_state_dir``), so an
# env var read when ``get_settings()`` is first called is the only override that works no matter
# when stabbur is imported — and it moves stabbur's dir only, leaving the HF cache alone.
os.environ["STABBUR_RUNTIME_STATE_DIR"] = str(
    Path(tempfile.mkdtemp(prefix="stabbur-test-runtime-")) / "stabbur" / "runtimes"
)

# Drop any upstream the developer exports. ``doctor.check_upstream`` probes ``settings.upstream``
# over the network, so a ``STABBUR_UPSTREAM`` in the environment would turn a hermetic doctor test
# into a live call against whatever box that names (and fail on a checkout that can't reach it).
os.environ.pop("STABBUR_UPSTREAM", None)

from stabbur import catalog  # noqa: E402 - after the env setup above, deliberately
from stabbur.sources import huggingface as _hf  # noqa: E402

_REAL_CATALOG_PULL = catalog.pull  # captured before the guard below can replace it
_REAL_PREFERRED_INCLUDE = _hf.preferred_include


@pytest.fixture(autouse=True)
def _no_real_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly rather than download: no test may reach a real pull.

    Nothing announces this failure mode. `stabbur setup` gained a step that fetches a starting
    model, and one CLI test that invoked it went from milliseconds to 165 seconds — silently
    pulling ~4 GB from the Hub on every run, in CI as well, while still passing. The suite is
    hermetic about the library, the config dir and the runtime state (above); it has to be
    hermetic about the network for the same reason.

    ``catalog.pull`` is the only choke point that needs blocking: every source, and
    ``wantlist.pull_entry`` above it, routes through it. A test that means to exercise a pull
    stubs it itself — its own ``monkeypatch.setattr`` runs after this one and wins.
    """

    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("a test tried to download a model. Stub the pull, or pass --no-download to `setup`.")

    monkeypatch.setattr(catalog, "pull", _blocked)
    # `pull` asks the Hub which quant to fetch when no include is given. That is a network call
    # inside a function tests exercise with a stubbed downloader, so it is neutralized here (no
    # filter) rather than left to reach out; a test that cares about the choice stubs it itself.
    monkeypatch.setattr(_hf, "preferred_include", lambda *a, **k: None)


@pytest.fixture
def real_pull(monkeypatch: pytest.MonkeyPatch) -> object:
    """Opt out of :func:`_no_real_downloads` for a pull that never reaches the network.

    Some pull paths are entirely local — an argument check that raises before fetching, a copy
    out of a seeded cache or another library — and those tests want the real function.
    """
    monkeypatch.setattr(catalog, "pull", _REAL_CATALOG_PULL)
    return _REAL_CATALOG_PULL


@pytest.fixture
def real_preferred_include(monkeypatch: pytest.MonkeyPatch) -> object:
    """Restore the real quant chooser for the tests that are about it (they stub the Hub)."""
    monkeypatch.setattr(_hf, "preferred_include", _REAL_PREFERRED_INCLUDE)
    return _REAL_PREFERRED_INCLUDE

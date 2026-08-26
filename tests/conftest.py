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

# Drop any upstream the developer exports. ``doctor.check_upstream`` probes ``settings.upstream``
# over the network, so a ``STABBUR_UPSTREAM`` in the environment would turn a hermetic doctor test
# into a live call against whatever box that names (and fail on a checkout that can't reach it).
os.environ.pop("STABBUR_UPSTREAM", None)

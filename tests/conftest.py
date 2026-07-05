"""Shared pytest fixtures / test-session setup.

Neutralize color-forcing env vars at import time — before any test module (and
thus ``kodo.cli``'s module-level Rich ``Console()``) is imported. CLI tests
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
# (e.g. a copy-paste ``ollama run`` command straddling the fold). Set before ``kodo.cli``'s
# module-level Console is constructed, same as the color vars above.
os.environ["COLUMNS"] = "200"

# Point the suite at an empty, throwaway library so it's hermetic. Without this the
# tests inherit whatever ``KODO_LIBRARY_ROOT`` the developer's ``.env`` supplies —
# which passes locally but fails on a clean checkout (CI) with ``LibraryNotConfigured``,
# and silently scans the real drive locally. An env var overrides ``.env``, so this wins
# everywhere. Tests that need the *unconfigured* state ``monkeypatch.delenv`` it explicitly.
os.environ.setdefault("KODO_LIBRARY_ROOT", tempfile.mkdtemp(prefix="kodo-test-library-"))

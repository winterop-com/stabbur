"""Shared pytest fixtures / test-session setup.

Neutralize color-forcing env vars at import time — before any test module (and
thus ``kodo.cli``'s module-level Rich ``Console()``) is imported. CLI tests
assert on substrings of Rich output; a ``FORCE_COLOR`` / ``CLICOLOR_FORCE`` in
the environment (some terminals and CI wrappers set it) makes Rich inject ANSI
escapes mid-string and breaks those assertions. Rich fixes the color system when
the Console is constructed, so this must run before that import, not in a fixture.
"""

import os

for _var in ("FORCE_COLOR", "CLICOLOR_FORCE"):
    os.environ.pop(_var, None)
os.environ["NO_COLOR"] = "1"

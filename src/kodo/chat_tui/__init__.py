"""Full-screen Textual chat for ``kodo chat`` (interactive mode).

Split into :mod:`._util` (helpers + constants), :mod:`._widgets` (command palette + input), and
:mod:`.app` (the ``ChatApp`` itself, one cohesive Textual App). ``run_interactive`` and ``ChatApp``
are re-exported so ``chat_tui.run_interactive(...)`` keeps working; the submodules are exposed for tests.
"""

from kodo.chat_tui import _util, _widgets, app
from kodo.chat_tui._widgets import ChatInput
from kodo.chat_tui.app import ChatApp, RemoteEndpoint, run_interactive

__all__ = ["run_interactive", "ChatApp", "ChatInput", "RemoteEndpoint", "app", "_widgets", "_util"]

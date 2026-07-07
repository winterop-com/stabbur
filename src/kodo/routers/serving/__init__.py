"""The serving API, split by concern behind one shared APIRouter.

:mod:`._base` owns the ``router`` and request-scoped dependencies; the route modules
(:mod:`.core`, :mod:`.chat`, :mod:`.voice`, :mod:`.proxy`) attach their endpoints to it on
import. The assembled ``router`` is re-exported here and mounted by :mod:`kodo.app`.

**Import order is significant**: routes register in import order, and :mod:`.proxy`'s
``/v1/{path}`` catch-all must come *after* the specific ``/v1/audio/*`` routes in :mod:`.voice`,
or it would shadow them. The request-scoped dependency functions are re-exported so tests (and
app wiring) can target them via ``serving.get_manager`` for ``dependency_overrides``.
"""

# Order matters — specific routes before proxy's /v1 catch-all (see module docstring).
from kodo.routers.serving import core, chat, voice, proxy  # noqa: F401,I001 - register routes, in order
from kodo.routers.serving._base import get_conf, get_http, get_lifecycle_lock, get_manager, router

__all__ = ["router", "core", "chat", "voice", "proxy", "get_manager", "get_http", "get_conf", "get_lifecycle_lock"]

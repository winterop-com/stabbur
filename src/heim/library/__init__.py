"""The on-drive model library: scan, resolve, and manage models under ``library_root``.

Split into focused private submodules — :mod:`._model` (identity + classification),
:mod:`._roots` (where the libraries live), :mod:`._scan` (reading models off disk + finding
them), and :mod:`._manage` (remove/migrate/verify) — with the public API re-exported here so
``from heim import library`` and ``library.scan()`` keep working unchanged. The submodules are
also exposed (``library._scan`` etc.) so tests can reach the internal helpers they need.
"""

from heim.library import _manage, _model, _roots, _scan
from heim.library._manage import MigrationAction, VerifyResult, apply_migration, plan_migration, remove, verify
from heim.library._model import LibraryModel, ModelRef, pick_gguf
from heim.library._roots import SHARED_TOKEN, LibraryNotConfigured, configured, default_root, roots
from heim.library._scan import find, find_copies, scan, tts_models

__all__ = [
    "_model",
    "_roots",
    "_scan",
    "_manage",
    "LibraryModel",
    "ModelRef",
    "pick_gguf",
    "LibraryNotConfigured",
    "SHARED_TOKEN",
    "configured",
    "default_root",
    "roots",
    "scan",
    "tts_models",
    "find_copies",
    "find",
    "MigrationAction",
    "VerifyResult",
    "remove",
    "plan_migration",
    "apply_migration",
    "verify",
]

"""Discovery of running ``kodo serve`` processes, so ``kodo chat`` can reuse a loaded model.

Each ``kodo serve`` writes a small record (its base URL + the model it locked + its pid) under the
machine runtime dir; ``kodo chat``, given no explicit ``--server``, looks here for a **live** server
serving the model it wants and attaches to it — reusing the resident model instead of spawning a
runtime and reloading the weights. Records are ephemeral, machine-local hints (siblings of the
supervisor's ``runtimes/``): a stale one (the serve died) is ignored via a pid check and swept.

Only **unauthenticated** serves register — a loopback ``kodo serve`` with no bearer token. A serve
that needs auth (non-loopback, or an explicit ``KODO_AUTH_TOKEN``) is left out, since ``kodo chat``
doesn't yet send the token; use ``--server`` explicitly there.
"""

import os
from pathlib import Path

from pydantic import BaseModel, ValidationError

from kodo.config import get_settings


class ServeRecord(BaseModel):
    """A running ``kodo serve``'s discovery record."""

    base_url: str
    model: str | None  # the locked model (None = free-play, not auto-attachable)
    pid: int


def _registry_dir() -> Path:
    """The directory holding serve records (sibling of the supervisor's ``runtimes/``)."""
    return get_settings().runtime_state_dir.parent / "serves"


def _pid_alive(pid: int) -> bool:
    """Whether ``pid`` is a live process (signal 0 probe)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists but owned by another user
        return True
    return True


def register(base_url: str, model: str | None) -> Path | None:
    """Record this ``kodo serve`` (its base URL + locked model) so ``kodo chat`` can find it.

    Best-effort: returns the record path, or None if it couldn't be written (e.g. a read-only dir).
    """
    path = _registry_dir() / f"{os.getpid()}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            ServeRecord(base_url=base_url, model=model, pid=os.getpid()).model_dump_json(), encoding="utf-8"
        )
        return path
    except OSError:
        return None


def unregister() -> None:
    """Remove this process's serve record (called when ``kodo serve`` stops)."""
    try:
        (_registry_dir() / f"{os.getpid()}.json").unlink(missing_ok=True)
    except OSError:
        pass


def discover(model: str) -> ServeRecord | None:
    """A live registered serve locked to ``model``, or None. Sweeps stale (dead-pid) records."""
    directory = _registry_dir()
    if not directory.is_dir():
        return None
    for record_file in directory.glob("*.json"):
        try:
            record = ServeRecord.model_validate_json(record_file.read_text(encoding="utf-8"))
        except (OSError, ValidationError):
            continue
        if not _pid_alive(record.pid):
            record_file.unlink(missing_ok=True)  # the serve is gone; clean up the stale hint
            continue
        if record.model == model:
            return record
    return None

"""The want list: export the library as a re-pullable TOML manifest, and sync one back.

The library is already a manifest — every pull records its source in a ``.heim/metadata.json``
sidecar, and Ollama models are reconstructable from their manifests. ``heim library manifest``
reads that back into a portable, human-editable list of ``[[model]]`` entries (source + name +
format); ``heim library sync`` diffs such a file against the library and re-pulls what's missing
via the normal per-source pull paths.

No state is kept in the library: the manifest is generated on demand and the user keeps the file
wherever they like (commit it, copy it to a new drive) — that's the whole rebuild-my-drive story.
``manifest --save models.toml`` today, ``sync models.toml`` on the new drive.

This module is the single reader (:func:`parse`) and writer (:func:`render`) of the want-list
file, mirroring the one-parser-one-writer discipline of ``heim.toml`` (:mod:`heim.project`).
"""

import json
import tomllib
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from heim import cards
from heim.library import LibraryModel
from heim.models import ModelFormat, ModelSource, PullResult

_HEADER = (
    "# heim want list - models to keep in this library.\n"
    "# Regenerate with:  heim library manifest --save <file>\n"
    "# Re-download with: heim library sync <file>\n"
)


class WantModel(BaseModel):
    """One model in a want list: enough to re-pull it from its source.

    ``note`` is presentation-only — rendered as a leading ``#`` comment, never a TOML key — so it
    doesn't survive a parse round-trip and is not part of the model's re-pull identity.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    name: str
    model_format: str = ""
    note: str = ""

    @property
    def ident(self) -> tuple[str, str, str]:
        """The ``(source, lowercased name, format)`` identity used to match against the library."""
        return (self.source, self.name.lower(), self.model_format)


class SyncPlan(BaseModel):
    """The diff of a want list against the current library: what's already there vs. still missing."""

    model_config = ConfigDict(frozen=True)

    present: list[WantModel] = []
    missing: list[WantModel] = []


def _sidecar_meta(model: LibraryModel) -> dict[str, Any]:
    """Read a directory model's ``.heim/metadata.json`` (records its source), or ``{}`` if absent."""
    path = model.path / cards.SIDECAR_DIR / "metadata.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _voice_id(repo: str) -> str | None:
    """The registry short id (e.g. ``kokoro``) for a voice model's repo name, if known."""
    from heim.voice import registry  # noqa: PLC0415 - keep voice deps lazy

    spec = registry.by_repo(repo)
    return spec.id if spec else None


def entry_for(model: LibraryModel) -> WantModel | str:
    """A :class:`WantModel` that re-pulls ``model``, or a comment string when it isn't re-pullable.

    Classification (also used by :func:`plan`, so a round-trip is stable):

    * Ollama models re-pull from the local Ollama store (``source = ollama``).
    * Voice models re-pull by their registry short id (``source = voice``); an unknown voice repo
      yields a comment instead.
    * Everything else is a Hugging Face repo — its library name *is* the repo id. LM Studio backups
      can't be re-downloaded as such, so they're recorded as their HF equivalent (with a note).
    """
    fmt = model.model_format.value if model.model_format is not ModelFormat.unknown else ""
    if model.voice_kind:
        vid = _voice_id(model.name)
        if vid is None:
            return f"voice model {model.name!r} — re-add with: heim library pull voice <id>"
        return WantModel(source=ModelSource.voice.value, name=vid)
    if model.is_ollama:
        return WantModel(source=ModelSource.ollama.value, name=model.name, model_format=ModelFormat.gguf.value)
    if _sidecar_meta(model).get("source") == ModelSource.lmstudio.value:
        return WantModel(
            source=ModelSource.huggingface.value,
            name=model.name,
            model_format=fmt,
            note="from an LM Studio backup (re-pulled from its Hugging Face equivalent)",
        )
    # Hugging Face, or an older pull with no sidecar: the library name is the HF repo id.
    return WantModel(source=ModelSource.huggingface.value, name=model.name, model_format=fmt)


def collect(models: Iterable[LibraryModel]) -> tuple[list[WantModel], list[str]]:
    """Split library models into re-pullable want entries + comment lines for the rest.

    Both lists are sorted for a stable, diff-friendly file.
    """
    entries: list[WantModel] = []
    comments: list[str] = []
    for model in models:
        result = entry_for(model)
        if isinstance(result, WantModel):
            entries.append(result)
        else:
            comments.append(result)
    entries.sort(key=lambda e: (e.source, e.name.lower()))
    comments.sort()
    return entries, comments


def render(entries: list[WantModel], comments: list[str] | None = None) -> str:
    """Render a want list as TOML — the single writer for this file (kept minimal, like ``heim.toml``).

    A per-entry ``note`` is emitted as a leading ``#`` comment; ``comments`` are trailing standalone
    lines for models that couldn't be turned into an entry.
    """
    parts = [_HEADER]
    for entry in entries:
        lines = ["\n"]
        if entry.note:
            lines.append(f"# {entry.note}\n")
        lines.append("[[model]]\n")
        lines.append(f"source = {json.dumps(entry.source)}\n")
        lines.append(f"name = {json.dumps(entry.name)}\n")
        if entry.model_format:
            lines.append(f"format = {json.dumps(entry.model_format)}\n")
        parts.append("".join(lines))
    if comments:
        parts.append("\n" + "".join(f"# {c}\n" for c in comments))
    return "".join(parts)


def parse(text: str) -> list[WantModel]:
    """Parse a want-list TOML file into :class:`WantModel` entries — the single reader.

    Raises :class:`ValueError` on a structurally invalid file (bad ``[[model]]`` shape or a missing
    ``source``/``name``); :class:`tomllib.TOMLDecodeError` propagates for malformed TOML.
    """
    raw = tomllib.loads(text).get("model", [])
    if not isinstance(raw, list):
        raise ValueError("'model' must be an array of tables ([[model]])")
    entries: list[WantModel] = []
    for item in raw:
        if not isinstance(item, dict) or "source" not in item or "name" not in item:
            raise ValueError("each [[model]] needs a 'source' and a 'name'")
        entries.append(
            WantModel(source=str(item["source"]), name=str(item["name"]), model_format=str(item.get("format", "")))
        )
    return entries


def plan(
    wants: list[WantModel],
    scanned: Iterable[LibraryModel],
    *,
    unhealthy: "Callable[[LibraryModel], bool] | None" = None,
) -> SyncPlan:
    """Diff want entries against the scanned library: which are already present vs. missing.

    A want entry is *present* when a scanned model classifies (via :func:`entry_for`) to the same
    identity — so a manifest exported from a library and synced back to it finds nothing to do.

    ``unhealthy`` powers the repair pass: a model it flags (a failed verification) is treated as
    **absent**, so its want lands in ``missing`` and sync re-pulls it over the damaged copy. A
    second, healthy copy of the same identity still counts as present — one good copy is enough.
    """
    have: set[tuple[str, str, str]] = set()
    for model in scanned:
        entry = entry_for(model)
        if not isinstance(entry, WantModel):
            continue
        if unhealthy is not None and unhealthy(model):
            continue  # damaged: leave the identity out so the want is re-pulled
        have.add(entry.ident)
    present = [w for w in wants if w.ident in have]
    missing = [w for w in wants if w.ident not in have]
    return SyncPlan(present=present, missing=missing)


def pull_entry(entry: WantModel, library_root: Path | None) -> PullResult:
    """Re-pull one want entry via the normal per-source path. The seam tests stub out.

    Routes every source through :func:`heim.catalog.pull` so sync reuses the exact pull machinery
    the CLI ``library pull`` command does — no re-implementation.
    """
    from heim import catalog  # noqa: PLC0415 - lazy: keeps this module's import light

    return catalog.pull(ModelSource(entry.source), entry.name, library_root=library_root)

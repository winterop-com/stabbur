"""The want list: export the library as a re-pullable TOML manifest, and sync one back.

The library is already a manifest — every pull records its source in a ``.stabbur/metadata.json``
sidecar, and Ollama models are reconstructable from their manifests. ``stabbur library manifest``
reads that back into a portable, human-editable list of ``[[model]]`` entries (source + name +
format, plus the ``include`` globs a partial pull used); ``stabbur library sync`` diffs such a file
against the library and re-pulls what's missing via the normal per-source pull paths.

No state is kept in the library: the manifest is generated on demand and the user keeps the file
wherever they like (commit it, copy it to a new drive) — that's the whole rebuild-my-drive story.
``manifest --save models.toml`` today, ``sync models.toml`` on the new drive.

This module is the single reader (:func:`parse`) and writer (:func:`render`) of the want-list
file, mirroring the one-parser-one-writer discipline of ``stabbur.toml`` (:mod:`stabbur.project`).
"""

import json
import tomllib
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from stabbur import cards
from stabbur.library import LibraryModel
from stabbur.models import ModelFormat, ModelSource, PullResult

# How far above a model directory to look for the sidecar that describes its pull. A repo that keeps
# each quant in its own folder (``<repo>/UD-Q3_K_XL/*.gguf``) is *scanned* at the quant folder, one
# level below the directory the pull wrote its sidecar into; three levels is slack for a deeper one.
_SIDECAR_LEVELS = 3

_HEADER = (
    "# stabbur want list - models to keep in this library.\n"
    "# Regenerate with:  stabbur library manifest --save <file>\n"
    "# Re-download with: stabbur library sync <file>\n"
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

    include: list[str] = []
    """Filename globs that fetch just this copy (Hugging Face) — empty means the whole repo.

    A multi-quant GGUF repo can be hundreds of GB while the copy on the drive is one quant, so a
    want list that only named the repo would rebuild the drive several times over. Not part of
    :attr:`ident`: an older want list has no ``include``, and must still match the model it
    describes rather than re-pulling it.
    """

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
    """Read a directory model's ``.stabbur/metadata.json`` (records its source), or ``{}`` if absent."""
    return _nearest_sidecar(model)[0]


def _nearest_sidecar(model: LibraryModel) -> tuple[dict[str, Any], str]:
    """The closest sidecar metadata at or above ``model.path``, and the path from it down to the model.

    A pull writes one sidecar, on the directory it downloaded into. That is usually the model
    directory — but a repo that ships each quant in its own folder is scanned at the *folder*, so
    the sidecar sits a level or two up. Walking up finds the pull that produced this model; the
    trailing path (``""`` when the sidecar is on the model itself) is the part of the repo this
    copy is, which is exactly what an ``include`` glob has to reproduce.
    """
    candidate = model.path
    for _ in range(_SIDECAR_LEVELS):
        meta = cards.read_metadata(cards.sidecar_dir(candidate))
        if meta:
            below = model.path.relative_to(candidate)
            return meta, "" if below == Path() else below.as_posix()
        parent = candidate.parent
        if parent == candidate or parent == model.library_root:
            break  # at the filesystem root, or about to step out of the library
        candidate = parent
    return {}, ""


def _split_repo(name: str) -> tuple[str, str]:
    """Split a library name into its ``<publisher>/<repo>`` id and any deeper sub-path.

    A Hugging Face repo id has exactly two segments. A library name with more is a model scanned
    inside a repo (``pub/Repo-GGUF/UD-Q3_K_XL``) — emitting that as a want-list ``name`` produced an
    entry no source could ever pull, because there is no such repo.
    """
    publisher, _, rest = name.partition("/")
    repo, _, below = rest.partition("/")
    return (f"{publisher}/{repo}" if rest else name), below


def _repo_and_include(model: LibraryModel) -> tuple[str, list[str]]:
    """The Hugging Face repo id that re-pulls ``model``, and the globs that fetch just this copy.

    The sidecar wins where it has an answer (it records what the pull asked for); the directory
    layout is the fallback for a pull that predates the recording, or for a model with no sidecar
    at all — a sub-path under the repo becomes ``<sub-path>/*``.
    """
    meta, below = _nearest_sidecar(model)
    repo, tail = _split_repo(model.name)
    recorded_name = meta.get("name")
    if isinstance(recorded_name, str) and recorded_name:
        repo = recorded_name
    recorded_include = meta.get("include")
    if isinstance(recorded_include, list) and recorded_include:
        return repo, [str(pattern) for pattern in recorded_include]
    sub = below or tail
    return repo, [f"{sub}/*"] if sub else []


def _voice_id(repo: str) -> str | None:
    """The registry short id (e.g. ``kokoro``) for a voice model's repo name, if known."""
    from stabbur.voice import registry  # noqa: PLC0415 - keep voice deps lazy

    spec = registry.by_repo(repo)
    return spec.id if spec else None


def entry_for(model: LibraryModel) -> WantModel | str:
    """A :class:`WantModel` that re-pulls ``model``, or a comment string when it isn't re-pullable.

    Classification (also used by :func:`plan`, so a round-trip is stable):

    * Ollama models re-pull from the local Ollama store (``source = ollama``).
    * Voice models re-pull by their registry short id (``source = voice``); an unknown voice repo
      yields a comment instead.
    * Everything else is a Hugging Face repo — its library name *is* the repo id, unless the model
      sits inside one (a per-quant folder), in which case the repo id plus an ``include`` glob is
      what re-pulls it. LM Studio backups can't be re-downloaded as such, so they're recorded as
      their HF equivalent (with a note).
    """
    fmt = model.model_format.value if model.model_format is not ModelFormat.unknown else ""
    if model.voice_kind:
        vid = _voice_id(model.name)
        if vid is None:
            return f"voice model {model.name!r} — re-add with: stabbur library pull voice <id>"
        return WantModel(source=ModelSource.voice.value, name=vid)
    if model.is_ollama:
        return WantModel(source=ModelSource.ollama.value, name=model.name, model_format=ModelFormat.gguf.value)
    repo, include = _repo_and_include(model)
    if _sidecar_meta(model).get("source") == ModelSource.lmstudio.value:
        return WantModel(
            source=ModelSource.huggingface.value,
            name=repo,
            model_format=fmt,
            include=include,
            note="from an LM Studio backup (re-pulled from its Hugging Face equivalent)",
        )
    # Hugging Face, or an older pull with no sidecar: the library name is the HF repo id.
    return WantModel(source=ModelSource.huggingface.value, name=repo, model_format=fmt, include=include)


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
    # Two quant folders of one repo collapse to the same repo id but different include globs, so
    # dedupe on the whole re-pull recipe rather than the name — and keep both when they differ.
    seen: set[tuple[str, str, str, tuple[str, ...]]] = set()
    unique: list[WantModel] = []
    for entry in sorted(entries, key=lambda e: (e.source, e.name.lower(), e.include)):
        key = (entry.source, entry.name.lower(), entry.model_format, tuple(entry.include))
        if key not in seen:
            seen.add(key)
            unique.append(entry)
    comments.sort()
    return unique, comments


def render(entries: list[WantModel], comments: list[str] | None = None) -> str:
    """Render a want list as TOML — the single writer for this file (kept minimal, like ``stabbur.toml``).

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
        if entry.include:
            lines.append(f"include = [{', '.join(json.dumps(p) for p in entry.include)}]\n")
        parts.append("".join(lines))
    if comments:
        parts.append("\n" + "".join(f"# {c}\n" for c in comments))
    return "".join(parts)


def parse(text: str) -> list[WantModel]:
    """Parse a want-list TOML file into :class:`WantModel` entries — the single reader.

    Raises :class:`ValueError` on a structurally invalid file (bad ``[[model]]`` shape, a missing
    ``source``/``name``, or an ``include`` that isn't a list of strings);
    :class:`tomllib.TOMLDecodeError` propagates for malformed TOML.
    """
    raw = tomllib.loads(text).get("model", [])
    if not isinstance(raw, list):
        raise ValueError("'model' must be an array of tables ([[model]])")
    entries: list[WantModel] = []
    for item in raw:
        if not isinstance(item, dict) or "source" not in item or "name" not in item:
            raise ValueError("each [[model]] needs a 'source' and a 'name'")
        include = item.get("include", [])
        if not isinstance(include, list) or not all(isinstance(p, str) for p in include):
            raise ValueError("'include' must be a list of filename globs, e.g. include = [\"*Q4_K_M*\"]")
        entries.append(
            WantModel(
                source=str(item["source"]),
                name=str(item["name"]),
                model_format=str(item.get("format", "")),
                include=list(include),
            )
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

    Routes every source through :func:`stabbur.catalog.pull` so sync reuses the exact pull machinery
    the CLI ``library pull`` command does — no re-implementation. An entry's ``include`` globs are
    passed through (Hugging Face only, which is the only source that has them), so rebuilding a
    drive fetches the one quant the want list describes, not the whole multi-quant repo.
    """
    from stabbur import catalog  # noqa: PLC0415 - lazy: keeps this module's import light

    source = ModelSource(entry.source)
    include = entry.include if source is ModelSource.huggingface and entry.include else None
    return catalog.pull(source, entry.name, library_root=library_root, include=include)

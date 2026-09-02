"""The project manifest (``stabbur.toml``) — read *and* write in one place.

A project declares which model to use, a system prompt, and which libraries it composes, so
``stabbur chat`` in a project directory picks them up without flags. Tools are **not** here: MCP
servers live in the standard ``mcpServers`` JSON (:mod:`stabbur.mcpservers`, ``./.mcp.json``).

This module is the **single owner** of the project side of ``stabbur.toml`` (A1):

* :func:`read_raw` is the one TOML parser — both this module and :mod:`stabbur.config` (which reads
  the *machine* settings, e.g. ``library_root``, from the same file) go through it, so a malformed
  file fails one way (a clean :class:`ProjectError`), not two.
* :func:`load` turns the parse into a validated :class:`Project` model.
* :func:`render_manifest` **owns writes** — a fresh file is rendered from values.

``stabbur.toml`` has two readers by design: *machine* settings (env-overridable, per-machine) live in
:class:`stabbur.config.Settings`; the *portable* assistant manifest (``[project]`` / ``[voice]`` /
``[assistant]`` / ``libraries``) lives here. Same file, two purposes, one parser.

The manifest is found by **walking up** from the working directory (:func:`discover`), the way ``git``,
``npm`` and every ``.mcp.json``-reading tool find theirs — see that function for the exact rule and its
boundaries. Because a project is therefore usually *above* the cwd, everything a manifest names is
resolved **relative to the manifest's own directory** (:attr:`Project.directory`), never to the cwd:
its ``libraries`` entries (:func:`stabbur.library.roots`) and its ``.mcp.json``
(:func:`stabbur.mcpservers.project_path`). A relative path in a manifest means what the scaffold
comment says it means — "relative to this file" — from anywhere inside the project.
"""

import json
import re
import tomllib
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

_DEFAULT_PATH = Path("stabbur.toml")


def _manifest_path(base: Path = Path()) -> Path:
    """The manifest to read under ``base``."""
    return base / _DEFAULT_PATH


def _home() -> Path | None:
    """The user's home directory, resolved — ``None`` when it can't be determined.

    Only a discovery *boundary* (see :func:`_search_dirs`), so an environment without a usable home
    (no ``HOME``, no passwd entry) must degrade to "one fewer stopping rule", never to a crash.
    """
    try:
        return Path.home().resolve()
    except (RuntimeError, OSError):
        return None


def _device(path: Path) -> int | None:
    """``path``'s filesystem device id, or ``None`` if it can't be stat'd (treated as "unknown")."""
    try:
        return path.stat().st_dev
    except OSError:
        return None


def _search_dirs(start: Path) -> Iterator[Path]:
    """Yield ``start`` and each parent that discovery is allowed to look in, nearest first.

    The walk stops on the first of three boundaries, so a manifest far away from where you stand can
    never quietly claim your shell:

    * **the filesystem root is never searched** — a ``stabbur.toml`` in ``/`` applies to nothing;
    * **home is the ceiling** — home itself is searched, its parent (``/Users``, ``/home``) is not;
    * **mount boundaries** — a ``st_dev`` change stops the walk, so standing in a project on an
      external drive never reaches back into the machine's own filesystem.
    """
    home = _home()
    start_device = _device(start)
    current = start
    while current.parent != current:  # the root itself is never a candidate
        yield current
        if home is not None and current.resolve() == home:
            return
        if start_device is not None and _device(current.parent) != start_device:
            return
        current = current.parent


def discover(start: Path | None = None) -> Path | None:
    """The nearest ``stabbur.toml`` at or above ``start`` (default: the working directory).

    Discovery walks up from ``start`` and returns the **first** directory's manifest, the way ``git``
    finds ``.git`` and every ``.mcp.json``-reading tool finds its config — so ``stabbur chat`` in
    ``myproject/src/`` binds to ``myproject``'s assistant instead of silently dropping to free-play.
    Returns ``None`` when no manifest is in scope. See :func:`_search_dirs` for the boundaries.

    The result is **absolute**, so a caller can always say *which* project applies — with one
    deliberate exception: a manifest in the working directory itself, found by the no-argument call,
    comes back as the plain relative ``Path("stabbur.toml")``. That is what every error message and
    hint printed before walk-up existed, and standing in the project root is the case where the full
    path adds nothing. Pass ``start`` explicitly to get an absolute path unconditionally.
    """
    bare = start is None
    start = Path.cwd() if start is None else start
    for index, directory in enumerate(_search_dirs(start)):
        candidate = _manifest_path(directory)
        if candidate.is_file():
            return _DEFAULT_PATH if bare and index == 0 else candidate.resolve()
    return None


def project_root(start: Path | None = None) -> Path | None:
    """The directory holding the nearest ``stabbur.toml``, or ``None`` outside a project.

    The base every project-relative path resolves against — ``libraries`` entries and ``.mcp.json``.
    """
    found = discover(start)
    return None if found is None else found.resolve().parent


_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+\Z")
"""A TOML *bare* key (no quoting needed): letters, digits, ``_`` and ``-`` only.

Anchored with ``\\Z`` (not ``$``) so a trailing newline can't sneak through — ``$`` matches *before*
a final ``\\n``, so ``"pat\\n"`` would look bare and emit an unquoted key with a raw newline (invalid
TOML), breaking the single-writer round-trip invariant."""

_SLUG_RE = re.compile(r"[^a-z0-9]+")
"""Runs of non-``[a-z0-9]`` characters, collapsed to a single ``-`` when slugifying a target name."""


def _slugify(name: str) -> str:
    """A URL/id-safe slug of ``name``: lower-cased, non-alphanumeric runs collapsed to ``-``, trimmed.

    Used to derive a target's stable ``id`` from its display name (``"Play 42"`` -> ``"play-42"``);
    returns ``""`` when nothing alphanumeric survives, so the caller can fall back to a positional id.
    """
    return _SLUG_RE.sub("-", name.lower()).strip("-")


class AssistantVerify(BaseModel):
    """How to verify the assistant's target instance: an MCP tool to run and its arguments.

    ``tool`` is a namespaced MCP tool (``<server>__<tool>``); stabbur runs it and reports the
    outcome, but never interprets it — a project decides what "verified" means for its domain.
    Unknown keys are kept (``extra="allow"``), matching the parent block's pass-through promise.
    """

    model_config = ConfigDict(extra="allow")

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    timeout: float = 20.0


_SAME_ORIGIN_PATH_RULE = "must be a same-origin relative path (start with '/', no scheme, no '..', no backslash)"


def _validate_same_origin_path(path: str) -> str:
    """Validate a same-origin, relative request path (used by probe / mint / revoke recipes).

    A UI client sends these against the *live session's* own origin, so they must stay relative and
    can't be coerced into an absolute/cross-origin URL: they start with a single ``/`` (not ``//``,
    which is protocol-relative), carry no scheme (no ``:`` before a ``?`` query), no ``..`` traversal
    and no backslash. A placeholder like ``{credential_id}`` is left intact (it violates none of these).
    """
    before_query = path.split("?", 1)[0]
    if not path.startswith("/") or path.startswith("//") or "\\" in path or ".." in path or ":" in before_query:
        raise ValueError(f"path {path!r} {_SAME_ORIGIN_PATH_RULE}")
    return path


# Public so :mod:`stabbur.routers.serving.assistant` derives its argv substitution from the *same*
# regex + allowed set (single source of truth — no second copy that could drift).
COMMAND_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")
ALLOWED_COMMAND_PLACEHOLDERS = frozenset({"base_url", "name"})

# Tokens a UI client fills in when minting / revoking a credential against the live session origin.
# Validated at parse time so a manifest typo (e.g. ``{expires_days}``) is caught at stabbur.toml load,
# not later in the browser at mint time.
ALLOWED_MINT_PAYLOAD_TOKENS = frozenset({"expires_ms", "allowed_methods", "description"})
ALLOWED_REVOKE_PATH_TOKENS = frozenset({"credential_id"})
# An identifier-style ``{token}`` — distinct from COMMAND_PLACEHOLDER_RE so JSON object braces in a
# mint_payload (``{"type":...}``) are not mistaken for placeholders (a ``{`` there is followed by ``"``).
_PLACEHOLDER_TOKEN_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _validate_command_placeholders(command: list[str]) -> list[str]:
    """Restrict argv ``{token}`` placeholders to ``{base_url}`` / ``{name}``.

    A bind mode's argv is templated from :class:`AssistantInfo` fields only; any other placeholder
    would either leak nothing or hint at an unsupported substitution, so it's rejected at parse time.
    """
    for arg in command:
        for token in COMMAND_PLACEHOLDER_RE.findall(arg):
            if token not in ALLOWED_COMMAND_PLACEHOLDERS:
                raise ValueError(f"command placeholder must be {{base_url}} or {{name}}, got {{{token}}}")
    return command


def _validate_placeholder_tokens(text: str, allowed: frozenset[str], field: str) -> None:
    """Restrict identifier-style ``{token}`` placeholders in ``text`` to ``allowed``.

    Catches a manifest typo like ``{expires_days}`` at parse time (stabbur.toml load) rather than
    letting it reach a UI client and fail at mint time in the browser. JSON object braces
    (``{"k":...}``) are not identifier tokens, so a mint_payload's own JSON is left untouched.
    """
    for token in _PLACEHOLDER_TOKEN_RE.findall(text):
        if token not in allowed:
            allowed_list = ", ".join("{" + t + "}" for t in sorted(allowed)) or "(none)"
            raise ValueError(f"{field} placeholder {{{token}}} is not allowed; allowed: {allowed_list}")


class AssistantProbe(BaseModel):
    """A same-origin session-probe recipe for UI clients; stabbur echoes it, never runs it.

    A UI client (the Chrome side panel) runs these read-only GETs against the *live browser session's*
    origin to identify who/what the session is bound to, then maps JSON out of the responses via
    ``fields`` (output name -> ordered ``"<pathIndex>.<jsonKey>"`` candidates, first hit wins) and
    formats ``label`` from them. Domain-generic: stabbur only carries and echoes it. Unknown keys are
    kept (``extra="allow"``), matching the parent block's pass-through promise.
    """

    model_config = ConfigDict(extra="allow")

    paths: list[str]
    fields: dict[str, list[str]] = Field(default_factory=dict)
    label: str = ""

    @field_validator("paths")
    @classmethod
    def _check_paths(cls, value: list[str]) -> list[str]:
        for path in value:
            _validate_same_origin_path(path)
        return value


_SECRET_ENV_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
"""A POSIX environment variable name: a letter/underscore start, then letters/digits/underscores."""

_FORBIDDEN_SECRET_ENV = frozenset(
    {"PATH", "PYTHONPATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH"}
)
"""Loader-controlling env vars a client-supplied secret must never be routed into (code-exec risk)."""


def _validate_secret_env_name(value: str, field: str) -> str:
    """Validate a client-supplied-secret env-var name: a valid identifier, never loader-controlling.

    Shared by ``secret_env`` and ``extra_secret_env`` so both route a caller's secret only into a
    plain env var — never ``PATH`` / ``LD_PRELOAD`` / ``DYLD_*``, which would let a caller who aimed
    the secret there control what the child loads and runs.
    """
    if not _SECRET_ENV_RE.match(value):
        raise ValueError(f"{field} must be a valid environment variable name, got {value!r}")
    if value in _FORBIDDEN_SECRET_ENV:
        raise ValueError(f"{field} must not be a loader-controlling variable, got {value!r}")
    return value


class BindMode(BaseModel):
    """One server-side handoff recipe: an argv stabbur runs (with the secret in an env var), plus how to undo it.

    ``command`` is templated from :class:`AssistantInfo` fields (``{base_url}`` / ``{name}`` only) and
    run with the caller's secret exported as ``secret_env``; ``unbind_command`` (optional) reverses it.
    These are execution details — stabbur runs them but never echoes them. Unknown keys are kept
    (``extra="allow"``).
    """

    model_config = ConfigDict(extra="allow")

    command: list[str]
    secret_env: str
    extra_secret_env: str | None = None
    """An optional secondary secret env var — a second value (e.g. a CSRF token alongside a session
    cookie) the caller may hand to this mode's child. Generic: stabbur never interprets it, it only
    exports it into the child env. Validated like ``secret_env`` (a valid identifier, never a
    loader-controlling variable)."""
    unbind_command: list[str] | None = None
    unbind_note: str | None = None
    timeout: float = Field(default=60.0, gt=0, le=300)
    """Seconds before the mode's argv is killed. Bounded (``0 < t <= 300``) so one bad mode can't
    hold the shared bind lock forever and starve every other bind/unbind call."""

    @field_validator("command", "unbind_command")
    @classmethod
    def _check_command(cls, value: list[str] | None) -> list[str] | None:
        return _validate_command_placeholders(value) if value is not None else value

    @field_validator("secret_env")
    @classmethod
    def _check_secret_env(cls, value: str) -> str:
        # The secret is client-supplied data handed to the child via this env var; it must be a valid
        # env-var name and must never be a loader-controlling variable (a caller who could aim the
        # secret at PATH / LD_PRELOAD / DYLD_* would control what the child loads and runs).
        return _validate_secret_env_name(value, "secret_env")

    @field_validator("extra_secret_env")
    @classmethod
    def _check_extra_secret_env(cls, value: str | None) -> str | None:
        # Same guarantees as secret_env (identifier, never loader-controlling) when present.
        return _validate_secret_env_name(value, "extra_secret_env") if value is not None else value

    @model_validator(mode="after")
    def _distinct_secret_envs(self) -> "BindMode":
        # The two secrets are exported in order (secret_env first, then extra_secret_env), so reusing
        # one name would silently overwrite the primary secret with the extra one in the child env.
        if self.extra_secret_env is not None and self.extra_secret_env == self.secret_env:
            raise ValueError("extra_secret_env must differ from secret_env")
        return self


class AssistantBind(BaseModel):
    """Browser-side mint recipe (echoed to clients) + server-side handoff modes (never echoed).

    The ``mint_*`` / ``revoke_*`` / ``methods_*`` fields describe how a UI client mints a scoped
    credential against the live session's own origin — stabbur only carries and echoes them, it never
    mints anything. ``modes`` maps a mode name to a :class:`BindMode` stabbur *does* run server-side to
    install the resulting secret. Unknown keys are kept (``extra="allow"``).
    """

    model_config = ConfigDict(extra="allow")

    mint_mode: str | None = None
    fallback_mode: str | None = None
    mint_path: str | None = None
    mint_method: str = "POST"
    mint_payload: str | None = None
    mint_token_field: str | None = None
    mint_id_field: str | None = None
    revoke_path: str | None = None
    expires_in_days: int = 30
    methods_readonly: list[str] = Field(default_factory=lambda: ["GET"])
    methods_full: list[str] = Field(default_factory=lambda: ["GET", "POST", "PUT", "PATCH", "DELETE"])
    session_cookie: str | None = None
    modes: dict[str, BindMode] = Field(default_factory=dict)

    @field_validator("mint_path", "revoke_path")
    @classmethod
    def _check_path(cls, value: str | None) -> str | None:
        return _validate_same_origin_path(value) if value is not None else value

    @field_validator("mint_payload")
    @classmethod
    def _check_mint_payload(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_placeholder_tokens(value, ALLOWED_MINT_PAYLOAD_TOKENS, "mint_payload")
        return value

    @field_validator("revoke_path")
    @classmethod
    def _check_revoke_tokens(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_placeholder_tokens(value, ALLOWED_REVOKE_PATH_TOKENS, "revoke_path")
        return value

    @field_validator("modes")
    @classmethod
    def _check_mode_names(cls, value: dict[str, BindMode]) -> dict[str, BindMode]:
        # Mode names become a TOML sub-table key ([assistant.bind.modes.<name>]); a non-bare name
        # would break the render/load round-trip, so reject it at parse time.
        for name in value:
            if not _BARE_KEY.match(name):
                raise ValueError(f"bind mode name must be a bare TOML key (letters/digits/_/-), got {name!r}")
        return value


class AssistantInfo(BaseModel):
    """Target-instance metadata for UI clients (``[assistant]``); stabbur echoes it, never interprets it.

    Domain-generic on purpose: fields like ``base_url`` / ``auth`` describe *some* backend the
    assistant targets, so a UI client (e.g. the Chrome side panel) can show it. Unknown keys are
    kept (``extra="allow"``) so a project can carry extra hints through a round-trip untouched.
    """

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    base_url: str | None = None
    auth: str | None = None
    readonly: bool | None = None
    source: str | None = None
    mcp_servers: list[str] = Field(default_factory=list)
    """The ``.mcp.json`` server names whose namespaced tools route to this target. Empty (the default,
    and the single-``[assistant]`` compat case) means the target owns *all* resolved servers — today's
    merged toolset. Naming servers here is how N targets partition the tools between them."""
    verify: AssistantVerify | None = None
    probe: AssistantProbe | None = None
    bind: AssistantBind | None = None
    # No per-target `id`: identity is owned solely by the registry (`AssistantRegistry.ids` / `by_id`),
    # which is the only thing that can make ids collision-safe across sibling targets. Derive an id there.


# Defined here (not imported at module top) so the project <-> targets import cycle resolves under either
# order: AssistantInfo is defined above, so targets can import it; AssistantRegistry is defined there before
# it imports us back. Project's `registry` field needs the concrete class, so import it now.
from stabbur.targets import AssistantRegistry  # noqa: E402


class Project(BaseModel):
    """A stabbur project (assistant) manifest. Tools live in ``.mcp.json`` (:mod:`stabbur.mcpservers`)."""

    model: str | None = None
    system_prompt: str = ""
    chat_voice: str | None = None
    """Voice for spoken replies in chat (e.g. ``kokoro:af_heart``); ``None`` = the UI default."""
    voice_enabled: bool = True
    """``[voice] enabled``; ``false`` hides the Voice surface for a pure-text assistant."""
    # Libraries this project uses, in priority order (read: first match wins).
    # Entries are paths relative to the project dir (e.g. ``.stabbur/library``), or the
    # token ``@shared`` for the machine's default library (``library_root``). Empty
    # → just the default library. See :func:`stabbur.library.roots`.
    libraries: list[str] = []
    # Target-instance metadata for UI clients; ``assistant`` is the **primary** target (the compat alias
    # every existing consumer reads), ``registry`` holds all targets (``[[assistants]]``) in priority order.
    # ``None`` / empty when no ``[assistant]`` or ``[[assistants]]`` block is present.
    assistant: AssistantInfo | None = None
    registry: AssistantRegistry = Field(default_factory=AssistantRegistry)
    manifest_path: Path | None = None
    """Where this manifest was found — set by :func:`load`, ``None`` for a hand-built ``Project``.

    Carried on the model because the manifest is usually *above* the cwd (:func:`discover`), so
    "relative to the project" and "relative to where I am" are different answers; consumers resolve
    against :attr:`directory`."""

    @property
    def directory(self) -> Path:
        """The directory every project-relative path resolves against.

        The manifest's own directory (so ``libraries = ["models"]`` means ``<project>/models`` from
        any subdirectory), falling back to the working directory for a ``Project`` built in memory.
        """
        return Path.cwd() if self.manifest_path is None else self.manifest_path.resolve().parent

    @model_validator(mode="after")
    def _sync_assistant_and_registry(self) -> "Project":
        """Keep ``assistant`` (the primary alias) and ``registry`` consistent however a Project is built.

        ``load()`` passes a fully-built ``registry``; older callers (and tests) pass a single ``assistant``.
        Whichever side is given, derive the other so ``proj.assistant is proj.registry.primary`` always
        holds — existing consumers keep reading ``assistant`` untouched.
        """
        if not self.registry.targets and self.assistant is not None:
            self.registry = AssistantRegistry(targets=[self.assistant])
        elif self.registry.targets and self.assistant is None:
            self.assistant = self.registry.primary
        return self


class ProjectError(RuntimeError):
    """A ``stabbur.toml`` that exists but can't be parsed or validated — surfaced cleanly, not as a traceback."""


# --- reading ---------------------------------------------------------------------------------


def read_raw(path: Path | None = None) -> dict[str, Any]:
    """Parse ``stabbur.toml`` into a raw dict — the one TOML parser for the whole app.

    Returns ``{}`` if the file doesn't exist. Both :func:`load` (the manifest) and
    :mod:`stabbur.config` (the machine settings) call this, so malformed TOML raises a single
    :class:`ProjectError` from one place instead of crashing differently in each reader.

    With no ``path`` the manifest is the discovered one (:func:`discover`) — the machine settings a
    project carries apply from its subdirectories too, exactly as its ``[project]`` block does.
    """
    path = discover() if path is None else path
    if path is None or not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ProjectError(f"{path} is not valid TOML: {exc}") from exc


def _require_table(value: Any, name: str, path: Path) -> dict[str, Any]:
    """The ``name`` block as a table, or a clean :class:`ProjectError` if it's some other type.

    ``voice = "loud"`` (or ``project = "x"``) is a manifest mistake, not a value to shrug off:
    reading it with ``.get`` would either silently ignore every key in the block or raise an
    ``AttributeError`` traceback. Missing blocks are fine — the caller passes ``{}`` for those.
    """
    if not isinstance(value, dict):
        raise ProjectError(f"{path}: {name} must be a table, got {value!r}")
    return value


def _require_bool(value: Any, name: str, path: Path) -> bool:
    """A manifest boolean, or a clean :class:`ProjectError`.

    ``bool()`` would coerce anything truthy, so ``enabled = "no"`` (a string) would read as
    *enabled* — the opposite of what was written. TOML has a real boolean, so require it.
    """
    if not isinstance(value, bool):
        raise ProjectError(f"{path}: {name} must be true or false, got {value!r}")
    return value


def _warn_manifest(message: str) -> None:
    """Warn once per distinct message about a manifest that loads but won't do what it says.

    ``stacklevel=1`` on purpose: :func:`load` runs several times per command (the CLI, then
    :func:`stabbur.library.roots`, then a scan), and pointing the warning at each *caller* would give
    every one its own registry entry — the same line repeated four times for one manifest.
    """
    warnings.warn(message, stacklevel=1)


def _read_libraries(value: Any, path: Path) -> list[str]:
    """The ``libraries`` list, validated as an array of strings.

    Coercing here is worse than failing: ``libraries = "../lib"`` (a string, not an array) would
    drop to ``[]`` and the project would silently read the *default* library instead of the one it
    named, and ``[1, 2]`` would coerce to nonsense paths. Both are typos — say so.
    """
    if not isinstance(value, list) or not all(isinstance(entry, str) for entry in value):
        raise ProjectError(f"{path}: libraries must be an array of strings, got {value!r}")
    for entry in value:
        # Not an error: a project may deliberately point at a fixed location (and @shared already
        # covers the portable case), but the manifest header promises no machine-specific paths.
        if entry != SHARED_LIBRARY_TOKEN and Path(entry).is_absolute():
            _warn_manifest(
                f"{path}: libraries entry {entry!r} is an absolute path, so this project is not "
                f"portable to another machine; use a project-relative path or '{SHARED_LIBRARY_TOKEN}'"
            )
    return value


def _warn_legacy_tools(data: dict[str, Any], path: Path) -> None:
    """Warn about pre-``.mcp.json`` tool config left in ``stabbur.toml``, which is now ignored.

    ``[[mcp]]`` / ``tools`` used to live in the manifest; tools moved to the standard ``mcpServers``
    JSON (:mod:`stabbur.mcpservers`). A leftover block parses fine and does nothing, so the project
    just runs without its tools — silently, unless we say something.
    """
    stale = [key for key in ("mcp", "tools") if key in data]
    if stale:
        names = " / ".join(f"'{key}'" for key in stale)
        _warn_manifest(
            f"{path}: {names} is ignored — MCP tool servers moved to .mcp.json; "
            "re-add them with `stabbur mcp add <name>`"
        )


def load(path: Path | None = None) -> Project | None:
    """Load the project manifest from ``path``, or ``None`` if there is no project in scope.

    With no ``path`` the manifest is the discovered one (:func:`discover`) — the nearest
    ``stabbur.toml`` at or above the working directory — and the loaded :class:`Project` remembers
    where it came from (:attr:`Project.manifest_path`), so its relative paths resolve against the
    project rather than against wherever the command happened to be run.

    Raises :class:`ProjectError` (with a readable message) on malformed TOML or a bad manifest —
    users hand-edit this file, so a typo must not crash every command. A wrong-*typed* value is a
    typo too, so it fails the same way rather than being coerced or dropped.
    """
    path = discover() if path is None else path
    if path is None or not path.is_file():
        return None
    data = read_raw(path)
    project = _require_table(data.get("project", {}), "[project]", path)
    voice = _require_table(data.get("voice", {}), "[voice]", path)
    libraries = _read_libraries(data.get("libraries", []), path)
    _warn_legacy_tools(data, path)
    single = data.get("assistant")
    array = data.get("assistants")
    # One shape or the other, never both — otherwise which is the primary is ambiguous.
    if single is not None and array is not None:
        raise ProjectError(f"{path} declares both [assistant] and [[assistants]]; use one or the other")
    try:
        # Each target is validated inside the try so a malformed one (bad verify, bad probe path, …)
        # fails like any other manifest value — a clean ProjectError, not a traceback.
        registry = _build_registry(single, array, path)
        return Project(
            model=project.get("model"),
            system_prompt=project.get("system_prompt", ""),
            chat_voice=project.get("chat_voice"),
            voice_enabled=_require_bool(voice.get("enabled", True), "[voice] enabled", path),
            libraries=libraries,
            registry=registry,
            manifest_path=path,
        )
    except (TypeError, ValidationError) as exc:
        raise ProjectError(f"{path} has an invalid value: {exc}") from exc


def _build_registry(single: Any, array: Any, path: Path) -> AssistantRegistry:
    """Build the assistant registry from a single ``[assistant]`` table or an ``[[assistants]]`` array.

    A single table becomes a one-target registry (the compat case); an array becomes one target per entry
    in declaration order. A ``[assistants]`` *table* (not an array-of-tables) is a manifest mistake and is
    rejected with a clear :class:`ProjectError`.
    """
    if array is not None:
        if not isinstance(array, list):
            raise ProjectError(
                f"{path}: [[assistants]] must be an array of tables (use [[assistants]], not [assistants])"
            )
        return AssistantRegistry(targets=[AssistantInfo.model_validate(entry) for entry in array])
    if single is not None:
        return AssistantRegistry(targets=[AssistantInfo.model_validate(single)])
    return AssistantRegistry()


def resolve_model(explicit: str | None, proj: "Project | None") -> str | None:
    """The model to use: explicit CLI name > project model > machine default.

    Outside a project (free-play), the machine default (``settings.default_model``, set via
    ``stabbur config set model``) supplies a model so ``stabbur chat`` / ``serve --ui`` have one to
    load without a project or an explicit argument. In a project, its ``model`` still wins.
    """
    from stabbur.config import get_settings  # noqa: PLC0415 - lazy: config imports project

    return explicit or (proj.model if proj else None) or get_settings().default_model


# --- writing (owned here so a write never leaves a broken stabbur.toml) -------------------------

SHARED_LIBRARY_TOKEN = "@shared"


def _render_string_or_list(value: Any) -> str:
    """Render a TOML value that must be a string or a list of strings.

    Shared by the ``[assistant.verify]`` args and the ``[assistant.probe]`` fields — both are
    ``string`` / ``list[str]`` maps. The single writer refuses anything else (``ProjectError``) so it
    never emits a stabbur.toml the single parser would then reject — keeping render/load a closed round-trip.
    """
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return "[" + ", ".join(json.dumps(x) for x in value) + "]"
    raise ProjectError(f"value must be a string or list of strings, got {value!r}")


def _render_assistant_value(key: str, value: Any) -> str:
    """Render one top-level ``[assistant]`` value as TOML: a finite scalar or list of scalars.

    ``json.dumps`` of a dict is JSON-object syntax (invalid TOML) and of a non-finite float is
    ``NaN``/``Infinity`` (also invalid) — the single writer refuses those (``ProjectError``)
    instead of emitting a manifest the single parser would reject.
    """
    scalar = (str, bool, int, float)
    finite = lambda v: not isinstance(v, float) or v == v and v not in (float("inf"), float("-inf"))  # noqa: E731
    if isinstance(value, scalar) and finite(value):
        return json.dumps(value)
    if isinstance(value, list) and all(isinstance(x, scalar) and finite(x) for x in value):
        return "[" + ", ".join(json.dumps(x) for x in value) + "]"
    raise ProjectError(f"[assistant] values must be scalars or lists of scalars, got {key} = {value!r}")


def _render_key(key: str) -> str:
    """A TOML key: bare when it is one, quoted otherwise.

    An unquoted non-bare key would emit invalid TOML (spaces, quotes) or a *different* value
    (``a.b`` becomes a dotted key, i.e. a nested table) — either breaks the render/load
    round-trip the single writer guarantees. TOML quoted keys are JSON-string compatible.
    """
    return key if _BARE_KEY.match(key) else json.dumps(key)


def _render_submodel(header: str, data: dict[str, Any], inline_table_keys: frozenset[str] = frozenset()) -> list[str]:
    """Render one ``[header]`` sub-table (blank-line separated) from an already-``exclude_none``'d dict.

    Each key in ``inline_table_keys`` renders as a TOML inline table of str / str-list values (verify's
    ``args``, probe's ``fields``); every other key renders as a scalar or list of scalars. This is the
    single writer for the verify / probe / bind sub-tables — bind's nested ``modes`` are rendered by the
    caller as further ``[assistant.bind.modes.<name>]`` sub-tables via a second call.
    """
    lines = ["", f"[{header}]"]
    for key, value in data.items():
        if key in inline_table_keys:
            inner = value if isinstance(value, dict) else {}
            inline = ", ".join(f"{_render_key(k)} = {_render_string_or_list(v)}" for k, v in inner.items())
            lines.append(f"{_render_key(key)} = {{" + (f" {inline} " if inline else "") + "}")
        else:
            lines.append(f"{_render_key(key)} = {_render_assistant_value(key, value)}")
    return lines


def _assistant_render_dict(assistant: "AssistantInfo") -> dict[str, Any]:
    """The ``exclude_none`` model dump minus the keys the writer never emits (empty ``mcp_servers``).

    An empty ``mcp_servers`` is the compat default, so dropping it keeps a single ``[assistant]`` render
    byte-identical to the pre-multi-target output. (``id`` is no longer a model field — identity lives on
    the registry — so there is nothing to strip for it.)
    """
    data = assistant.model_dump(exclude_none=True)
    if not data.get("mcp_servers"):
        data.pop("mcp_servers", None)
    return data


def _render_assistant_tables(header: str, data: dict[str, Any], lines: list[str]) -> None:
    """Append the verify / probe / bind sub-tables for one assistant to ``lines`` (``header`` is its prefix).

    Shared by the single ``[assistant]`` and the ``[[assistants]]`` array paths — only the header prefix
    differs (``assistant`` vs ``assistants``), so the nested sub-table rendering stays one implementation.
    """
    verify = data.pop("verify", None)
    probe = data.pop("probe", None)
    bind = data.pop("bind", None)
    lines.extend(f"{_render_key(key)} = {_render_assistant_value(key, value)}" for key, value in data.items())
    if verify is not None:
        lines.extend(_render_submodel(f"{header}.verify", verify, frozenset({"args"})))
    if probe is not None:
        lines.extend(_render_submodel(f"{header}.probe", probe, frozenset({"fields"})))
    if bind is not None:
        modes = bind.pop("modes", {})
        lines.extend(_render_submodel(f"{header}.bind", bind))
        for name, mode in modes.items():
            lines.extend(_render_submodel(f"{header}.bind.modes.{_render_key(name)}", mode))


def _render_assistant(assistant: "AssistantInfo") -> str:
    """Render the ``[assistant]`` table (plus ``[assistant.verify]`` / ``[assistant.probe]`` / ``[assistant.bind]``).

    Scalars go through :func:`json.dumps` (valid TOML for str/bool/int/float) and keys through
    :func:`_render_key`; the sub-tables (verify / probe / bind, with a table per bind mode) are
    rendered after every top-level ``[assistant]`` key so the flat keys close before any sub-table opens.
    """
    lines = [
        "# [assistant] - target metadata for UI clients; stabbur echoes it, never interprets it.",
        "[assistant]",
    ]
    _render_assistant_tables("assistant", _assistant_render_dict(assistant), lines)
    return "\n".join(lines) + "\n"


def _render_assistants(targets: "list[AssistantInfo]") -> str:
    """Render N targets as a TOML ``[[assistants]]`` array-of-tables (each with its nested sub-tables).

    Each entry opens a fresh ``[[assistants]]`` element; its verify / probe / bind sub-tables attach to that
    element via the ``assistants.*`` header prefix (TOML binds them to the most recent array entry). This is
    the multi-target write path; a single target is still rendered as a plain ``[assistant]`` table via
    :func:`_render_assistant` to keep the common case byte-identical.
    """
    lines = ["# [[assistants]] - target metadata for UI clients; stabbur echoes it, never interprets it."]
    for target in targets:
        lines.extend(["", "[[assistants]]"])
        _render_assistant_tables("assistants", _assistant_render_dict(target), lines)
    return "\n".join(lines) + "\n"


def render_manifest(
    *,
    model: str,
    system_prompt: str = "",
    local_library_dir: str | None = None,
    chat_voice: str | None = None,
    assistant: "AssistantInfo | None" = None,
    registry: "AssistantRegistry | None" = None,
) -> str:
    """Render a fresh ``stabbur.toml`` from values (used by ``project init`` / ``project new``).

    Portable and git-committable — no machine-specific paths. ``local_library_dir`` names the
    project's own store (created alongside this file) and is listed **alone**: a scaffolded project
    is self-contained, so it must not read the machine's library at all. A project that inherited
    ``@shared`` would keep working on the machine that made it and break the moment the directory
    was moved — the one thing a self-contained project promises not to do. Add the token by hand
    to opt back in. ``None`` means the project uses only the machine library. ``[project]`` defines
    the assistant; tools live in ``.mcp.json``. Override per machine with ``STABBUR_*``.
    """
    if local_library_dir:
        libraries_block = (
            f'# This project reads only its own "{local_library_dir}/" store, so it travels intact.\n'
            f'# To also read the machine library, add "{SHARED_LIBRARY_TOKEN}" to this list.\n'
            f'libraries = ["{local_library_dir}"]\n\n'
        )
    else:
        libraries_block = (
            "# Uses your machine library (STABBUR_LIBRARY_ROOT). To also read a project-local\n"
            f'# store, add:  libraries = ["models", "{SHARED_LIBRARY_TOKEN}"]  (relative to this file).\n\n'
        )
    # Kokoro (tiny) is the default speak-replies voice for every project, so any assistant
    # can talk back without loading a second multi-GB model.
    voice_line = f"chat_voice = {json.dumps(chat_voice)}  # spoken-reply voice (Kokoro)\n" if chat_voice else ""
    # A registry (multiple targets) writes the [[assistants]] array; a single `assistant=` keeps the
    # byte-identical [assistant] table. A one-target registry still writes the array form (it came from
    # [[assistants]]); the single-table form is reserved for the `assistant=` shim so scaffolds are stable.
    if registry is not None and registry.targets:
        assistant_block = f"\n{_render_assistants(registry.targets)}"
    elif assistant is not None:
        assistant_block = f"\n{_render_assistant(assistant)}"
    else:
        assistant_block = ""
    return (
        "# stabbur project — a purpose-built assistant (model + system prompt).\n"
        "# Portable + committable: no machine-specific paths. Tools live in .mcp.json.\n\n"
        f"{libraries_block}"
        "[project]\n"
        f"model = {json.dumps(model)}\n"
        f"system_prompt = {json.dumps(system_prompt)}\n"
        f"{voice_line}"
        f"{assistant_block}"
    )

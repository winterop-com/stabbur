"""The project manifest (``kodo.toml``) — read *and* write in one place.

A project declares which model to use, a system prompt, and which libraries it composes, so
``kodo chat`` in a project directory picks them up without flags. Tools are **not** here: MCP
servers live in the standard ``mcpServers`` JSON (:mod:`kodo.mcpservers`, ``./.mcp.json``).

This module is the **single owner** of the project side of ``kodo.toml`` (A1):

* :func:`read_raw` is the one TOML parser — both this module and :mod:`kodo.config` (which reads
  the *machine* settings, e.g. ``library_root``, from the same file) go through it, so a malformed
  file fails one way (a clean :class:`ProjectError`), not two.
* :func:`load` turns the parse into a validated :class:`Project` model.
* :func:`render_manifest` **owns writes** — a fresh file is rendered from values.

``kodo.toml`` has two readers by design: *machine* settings (env-overridable, per-machine) live in
:class:`kodo.config.Settings`; the *portable* assistant manifest (``[project]`` / ``[voice]`` /
``[assistant]`` / ``libraries``) lives here. Same file, two purposes, one parser.
"""

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

_DEFAULT_PATH = Path("kodo.toml")

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+\Z")
"""A TOML *bare* key (no quoting needed): letters, digits, ``_`` and ``-`` only.

Anchored with ``\\Z`` (not ``$``) so a trailing newline can't sneak through — ``$`` matches *before*
a final ``\\n``, so ``"pat\\n"`` would look bare and emit an unquoted key with a raw newline (invalid
TOML), breaking the single-writer round-trip invariant."""


class AssistantVerify(BaseModel):
    """How to verify the assistant's target instance: an MCP tool to run and its arguments.

    ``tool`` is a namespaced MCP tool (``<server>__<tool>``); kodo runs it and reports the
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


# Public so :mod:`kodo.routers.serving.assistant` derives its argv substitution from the *same*
# regex + allowed set (single source of truth — no second copy that could drift).
COMMAND_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")
ALLOWED_COMMAND_PLACEHOLDERS = frozenset({"base_url", "name"})

# Tokens a UI client fills in when minting / revoking a credential against the live session origin.
# Validated at parse time so a manifest typo (e.g. ``{expires_days}``) is caught at kodo.toml load,
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

    Catches a manifest typo like ``{expires_days}`` at parse time (kodo.toml load) rather than
    letting it reach a UI client and fail at mint time in the browser. JSON object braces
    (``{"k":...}``) are not identifier tokens, so a mint_payload's own JSON is left untouched.
    """
    for token in _PLACEHOLDER_TOKEN_RE.findall(text):
        if token not in allowed:
            allowed_list = ", ".join("{" + t + "}" for t in sorted(allowed)) or "(none)"
            raise ValueError(f"{field} placeholder {{{token}}} is not allowed; allowed: {allowed_list}")


class AssistantProbe(BaseModel):
    """A same-origin session-probe recipe for UI clients; kodo echoes it, never runs it.

    A UI client (the Chrome side panel) runs these read-only GETs against the *live browser session's*
    origin to identify who/what the session is bound to, then maps JSON out of the responses via
    ``fields`` (output name -> ordered ``"<pathIndex>.<jsonKey>"`` candidates, first hit wins) and
    formats ``label`` from them. Domain-generic: kodo only carries and echoes it. Unknown keys are
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
    """One server-side handoff recipe: an argv kodo runs (with the secret in an env var), plus how to undo it.

    ``command`` is templated from :class:`AssistantInfo` fields (``{base_url}`` / ``{name}`` only) and
    run with the caller's secret exported as ``secret_env``; ``unbind_command`` (optional) reverses it.
    These are execution details — kodo runs them but never echoes them. Unknown keys are kept
    (``extra="allow"``).
    """

    model_config = ConfigDict(extra="allow")

    command: list[str]
    secret_env: str
    extra_secret_env: str | None = None
    """An optional secondary secret env var — a second value (e.g. a CSRF token alongside a session
    cookie) the caller may hand to this mode's child. Generic: kodo never interprets it, it only
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
    credential against the live session's own origin — kodo only carries and echoes them, it never
    mints anything. ``modes`` maps a mode name to a :class:`BindMode` kodo *does* run server-side to
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
    """Target-instance metadata for UI clients (``[assistant]``); kodo echoes it, never interprets it.

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
    verify: AssistantVerify | None = None
    probe: AssistantProbe | None = None
    bind: AssistantBind | None = None


class Project(BaseModel):
    """A kodo project (assistant) manifest. Tools live in ``.mcp.json`` (:mod:`kodo.mcpservers`)."""

    model: str | None = None
    system_prompt: str = ""
    chat_voice: str | None = None
    """Voice for spoken replies in chat (e.g. ``kokoro:af_heart``); ``None`` = the UI default."""
    voice_enabled: bool = True
    """``[voice] enabled``; ``false`` hides the Voice surface for a pure-text assistant."""
    # Libraries this project uses, in priority order (read: first match wins).
    # Entries are paths relative to the project dir (e.g. ``.kodo/library``), or the
    # token ``@shared`` for the machine's default library (``library_root``). Empty
    # → just the default library. See :func:`kodo.library.roots`.
    libraries: list[str] = []
    # Target-instance metadata for UI clients (``[assistant]``); ``None`` when the block is absent.
    assistant: AssistantInfo | None = None


class ProjectError(RuntimeError):
    """A ``kodo.toml`` that exists but can't be parsed or validated — surfaced cleanly, not as a traceback."""


# --- reading ---------------------------------------------------------------------------------


def read_raw(path: Path = _DEFAULT_PATH) -> dict[str, Any]:
    """Parse ``kodo.toml`` into a raw dict — the one TOML parser for the whole app.

    Returns ``{}`` if the file doesn't exist. Both :func:`load` (the manifest) and
    :mod:`kodo.config` (the machine settings) call this, so malformed TOML raises a single
    :class:`ProjectError` from one place instead of crashing differently in each reader.
    """
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ProjectError(f"{path} is not valid TOML: {exc}") from exc


def load(path: Path = _DEFAULT_PATH) -> Project | None:
    """Load the project manifest from ``path``, or ``None`` if the file doesn't exist.

    Raises :class:`ProjectError` (with a readable message) on malformed TOML or a bad manifest —
    users hand-edit this file, so a typo must not crash every command.
    """
    if not path.is_file():
        return None
    data = read_raw(path)
    project = data.get("project", {})
    voice = data.get("voice", {})
    libraries = data.get("libraries", [])
    assistant = data.get("assistant")
    try:
        return Project(
            model=project.get("model"),
            system_prompt=project.get("system_prompt", ""),
            chat_voice=project.get("chat_voice"),
            voice_enabled=bool(voice.get("enabled", True)) if isinstance(voice, dict) else True,
            libraries=[str(x) for x in libraries] if isinstance(libraries, list) else [],
            # A malformed [assistant] block (e.g. a bad verify) must fail like any other manifest
            # value — a clean ProjectError, not a traceback — so it's validated inside the try.
            assistant=AssistantInfo.model_validate(assistant) if assistant is not None else None,
        )
    except (TypeError, ValidationError) as exc:
        raise ProjectError(f"{path} has an invalid value: {exc}") from exc


def resolve_model(explicit: str | None, proj: "Project | None") -> str | None:
    """The model to use: explicit CLI name > project model > machine default.

    Outside a project (free-play), the machine default (``settings.default_model``, set via
    ``kodo config set model``) supplies a model so ``kodo chat`` / ``serve --ui`` have one to
    load without a project or an explicit argument. In a project, its ``model`` still wins.
    """
    from kodo.config import get_settings  # noqa: PLC0415 - lazy: config imports project

    return explicit or (proj.model if proj else None) or get_settings().default_model


# --- writing (owned here so a write never leaves a broken kodo.toml) -------------------------

SHARED_LIBRARY_TOKEN = "@shared"


def _render_string_or_list(value: Any) -> str:
    """Render a TOML value that must be a string or a list of strings.

    Shared by the ``[assistant.verify]`` args and the ``[assistant.probe]`` fields — both are
    ``string`` / ``list[str]`` maps. The single writer refuses anything else (``ProjectError``) so it
    never emits a kodo.toml the single parser would then reject — keeping render/load a closed round-trip.
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


def _render_assistant(assistant: "AssistantInfo") -> str:
    """Render the ``[assistant]`` table (plus ``[assistant.verify]`` / ``[assistant.probe]`` / ``[assistant.bind]``).

    Scalars go through :func:`json.dumps` (valid TOML for str/bool/int/float) and keys through
    :func:`_render_key`; the sub-tables (verify / probe / bind, with a table per bind mode) are
    rendered after every top-level ``[assistant]`` key so the flat keys close before any sub-table opens.
    """
    data = assistant.model_dump(exclude_none=True)
    verify = data.pop("verify", None)
    probe = data.pop("probe", None)
    bind = data.pop("bind", None)
    lines = [
        "# [assistant] - target metadata for UI clients; kodo echoes it, never interprets it.",
        "[assistant]",
    ]
    lines.extend(f"{_render_key(key)} = {_render_assistant_value(key, value)}" for key, value in data.items())
    if verify is not None:
        lines.extend(_render_submodel("assistant.verify", verify, frozenset({"args"})))
    if probe is not None:
        lines.extend(_render_submodel("assistant.probe", probe, frozenset({"fields"})))
    if bind is not None:
        modes = bind.pop("modes", {})
        lines.extend(_render_submodel("assistant.bind", bind))
        for name, mode in modes.items():
            lines.extend(_render_submodel(f"assistant.bind.modes.{_render_key(name)}", mode))
    return "\n".join(lines) + "\n"


def render_manifest(
    *,
    model: str,
    system_prompt: str = "",
    local_library_dir: str | None = None,
    chat_voice: str | None = None,
    assistant: "AssistantInfo | None" = None,
) -> str:
    """Render a fresh ``kodo.toml`` from values (used by ``project init`` / ``project new``).

    Portable and git-committable — no machine-specific paths. ``libraries`` lists a project-local
    store (``local_library_dir``, created alongside this file) plus ``@shared``, the token for the
    machine's default library (``KODO_LIBRARY_ROOT``); ``None`` means the project uses only the
    shared library. ``[project]`` defines the assistant; tools live in ``.mcp.json``. Override
    anything per machine with ``KODO_*``.
    """
    if local_library_dir:
        libraries_block = (
            f'# This project ships its own "{local_library_dir}/" store (the model was downloaded there);\n'
            "# @shared is the machine default library (KODO_LIBRARY_ROOT) if you set one.\n"
            f'libraries = ["{local_library_dir}", "{SHARED_LIBRARY_TOKEN}"]\n\n'
        )
    else:
        libraries_block = (
            "# Uses your machine library (KODO_LIBRARY_ROOT). To also read a project-local\n"
            f'# store, add:  libraries = ["models", "{SHARED_LIBRARY_TOKEN}"]  (relative to this file).\n\n'
        )
    # Kokoro (tiny) is the default speak-replies voice for every project, so any assistant
    # can talk back without loading a second multi-GB model.
    voice_line = f"chat_voice = {json.dumps(chat_voice)}  # spoken-reply voice (Kokoro)\n" if chat_voice else ""
    assistant_block = f"\n{_render_assistant(assistant)}" if assistant is not None else ""
    return (
        "# kodo project — a purpose-built assistant (model + system prompt).\n"
        "# Portable + committable: no machine-specific paths. Tools live in .mcp.json.\n\n"
        f"{libraries_block}"
        "[project]\n"
        f"model = {json.dumps(model)}\n"
        f"system_prompt = {json.dumps(system_prompt)}\n"
        f"{voice_line}"
        f"{assistant_block}"
    )

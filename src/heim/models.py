"""Shared Pydantic models for the catalog and API."""

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field


class ModelSource(StrEnum):
    """A place models are downloaded from / stored by."""

    huggingface = "huggingface"
    ollama = "ollama"
    lmstudio = "lmstudio"
    voice = "voice"  # heim's registry of TTS/STT models (by short id, e.g. "kokoro"); lands in <root>/voice/


class ModelFormat(StrEnum):
    """The on-disk weight format, which determines the runtimes that can use it."""

    gguf = "gguf"
    """llama.cpp quant — runs on Ollama, LM Studio, llama.cpp (Mac + Linux)."""

    mlx = "mlx"
    """Apple Silicon native (safetensors-based) — LM Studio on Mac, mlx_lm."""

    safetensors = "safetensors"
    """Original full-precision weights (convert / fine-tune source)."""

    unknown = "unknown"


def _human_size(num_bytes: int) -> str:
    """Format a byte count as a human-readable string."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


class ModelEntry(BaseModel):
    """A single model discovered in a source store or in the backup root."""

    source: ModelSource
    name: str
    """Repo id (HF), ``model:tag`` (Ollama), or folder/file name (LM Studio)."""

    model_format: ModelFormat = ModelFormat.unknown
    generative: bool = True
    """Whether this is a generative chat LLM (vs an embedding/vision encoder)."""

    path: Path
    """Absolute path to the model on the local filesystem."""

    size_bytes: int = 0
    file_count: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def size_human(self) -> str:
        """Human-readable size of the model on disk."""
        return _human_size(self.size_bytes)


class Catalog(BaseModel):
    """A snapshot of all models discovered across sources."""

    entries: list[ModelEntry] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_bytes(self) -> int:
        """Sum of the size of every entry."""
        return sum(entry.size_bytes for entry in self.entries)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_human(self) -> str:
        """Human-readable total size of the catalog."""
        return _human_size(self.total_bytes)


class PullResult(BaseModel):
    """Outcome of pulling a single model into the library."""

    source: ModelSource
    name: str
    model_format: ModelFormat = ModelFormat.unknown
    destination: Path
    size_bytes: int = 0
    file_count: int = 0
    card_path: Path | None = None
    """Path to the model card / instructions, when one was found or generated."""

    metadata_path: Path | None = None
    """Path to the ``metadata.json`` sidecar written alongside the model."""

    source_removed: bool = False
    """True only if ``--move`` actually deleted the local source after a verified copy."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def size_human(self) -> str:
        """Human-readable size of the backed-up model."""
        return _human_size(self.size_bytes)


class HubModel(BaseModel):
    """A model found on the Hugging Face Hub (a `heim pull` candidate)."""

    id: str
    downloads: int = 0
    likes: int = 0
    size_bytes: int = 0  # approx bytes heim would pull (preferred quant); 0 = unknown


class CuratedModel(BaseModel):
    """A curated starter model offered by `heim init`."""

    model_config = ConfigDict(frozen=True)

    id: str
    note: str


class CuratedMcp(BaseModel):
    """A curated MCP tool server offered by `heim mcp list` / added by `heim mcp add`."""

    model_config = ConfigDict(frozen=True)

    name: str  # tool namespace + the .mcp.json server key, e.g. "dhis2"
    command: str  # the command to run it (added to .mcp.json by `heim mcp add`)
    description: str
    setup: str = ""  # one-line hint when the server needs config (a profile, path, key, Node, …)


class McpSettingKind(StrEnum):
    """What kind of value a setting holds — the whole of what a client needs to pick a control."""

    text = "text"
    """Free text (a host allowlist, a backend name)."""

    path = "path"
    """A filesystem location; ``effective`` is absolute, so a client can show where it points."""

    boolean = "boolean"
    """An on/off flag; ``default`` and ``effective`` are always ``"true"`` / ``"false"``."""


class McpSetting(BaseModel):
    """One environment variable a bundled MCP server understands, and what it is set to right now.

    A server's behaviour hangs on its env (``HEIM_FILES_ROOT`` decides the *only* directory the
    assistant can see), yet nothing outside the server's own source said so — leaving "a configured
    workspace root" as the only answer a UI could give, and hand-editing ``mcp.json`` as the only way
    to change it. A server declares these alongside its command (see :meth:`heim.plugins.Specs.mcp_servers`)
    and :func:`heim.mcp_catalog.bundled` fills in :attr:`effective`.

    Every value is a **string** because that is the only thing a spawned process can be handed —
    ``default`` and ``effective`` are what would literally be in the child's environment.
    """

    model_config = ConfigDict(frozen=True)

    env: str  # the variable itself, e.g. "HEIM_FILES_ROOT"
    label: str  # short human name for the control, e.g. "Workspace root"
    description: str = ""  # one line: what it changes, and what happens when it is unset
    type: McpSettingKind = McpSettingKind.text
    default: str = ""  # what the server falls back to when the variable is unset
    effective: str = ""
    """What is actually in force: the configured value, else the *resolved* default — a relative
    path default resolved against the directory ``heim serve`` runs in, since "``.``" is precisely
    the answer that leaves a user guessing which directory the assistant can browse."""


class BundledMcp(BaseModel):
    """One first-party MCP server heim ships, plus whether it is currently switched on.

    The unit the Tools UI renders: heim bundles a dozen ``heim-mcp-*`` servers, but until one is
    named in an ``mcp.json`` it is invisible — so this pairs the *shipped* set (from the plugins'
    own advertisements, never a second hardcoded list) with the *resolved* on/off state, letting a
    client show "here is everything heim can do, these are on" instead of an empty list.
    """

    model_config = ConfigDict(frozen=True)

    name: str  # tool namespace + the mcp.json server key, e.g. "datetime"
    command: str  # the console script that runs it, e.g. "heim-mcp-datetime"
    description: str = ""
    enabled: bool = False  # in the resolved global+project set, i.e. heim spawns it
    scope: str | None = None  # "global" | "project": which file switches it on (None when off)
    installed: bool = True  # False = an optional first-party server whose extra isn't installed yet
    setup: str = ""  # install hint, only when `installed` is False
    env: dict[str, str] = Field(default_factory=dict)
    """The env persisted for this server in the ``mcp.json`` entry that resolves it — usually empty
    (an entry is just a command until someone configures it). Configured vs *effective* is the whole
    distinction :class:`McpSetting` exists to draw: this is what is written down, `settings` is what
    is in force."""

    settings: list[McpSetting] = Field(default_factory=list)  # the env knobs it declares, each with its value


class ProjectTemplate(BaseModel):
    """A named starter for `heim project new --template <name>`.

    Presets the wizard so a purpose-built project (e.g. a DHIS2 assistant) is reproducible in
    one command: a default model, system prompt, MCP tools, and extra files to drop in.
    """

    model_config = ConfigDict(frozen=True)

    model: str  # default library model to bind (overridable with --model)
    system_prompt: str
    mcp: list[tuple[str, str]] = Field(default_factory=list)  # (name, command) written to .mcp.json
    files: dict[str, str] = Field(default_factory=dict)  # relative path -> content, written verbatim
    next_steps: str = ""  # printed after scaffolding (setup the template still needs)
    extras: list[str] = Field(default_factory=list)  # heim extras the uv project needs, e.g. ["voice"], ["web"]
    chat_voice: str | None = None  # spoken-reply voice override (default: kokoro:af_heart)
    # Opaque [assistant] target metadata (validated to AssistantInfo at scaffold time); a plain
    # dict here so models.py needn't import heim.project.AssistantInfo — avoids an import cycle.
    assistant: dict[str, Any] | None = Field(default=None)
    # Multi-target variant: a list of [[assistants]] blocks (validated to an AssistantRegistry at
    # scaffold time). A template sets ``assistant`` (single) OR ``assistants`` (a registry), not both.
    assistants: list[dict[str, Any]] | None = Field(default=None)


class ErrorResponse(BaseModel):
    """Standard error response."""

    detail: str
    code: str = "UNKNOWN_ERROR"

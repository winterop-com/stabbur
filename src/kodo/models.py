"""Shared Pydantic models for the catalog and API."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, computed_field


class ModelSource(StrEnum):
    """A place models are downloaded from / stored by."""

    huggingface = "huggingface"
    ollama = "ollama"
    lmstudio = "lmstudio"


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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def size_human(self) -> str:
        """Human-readable size of the backed-up model."""
        return _human_size(self.size_bytes)


class HubModel(BaseModel):
    """A model found on the Hugging Face Hub (a `kodo pull` candidate)."""

    id: str
    downloads: int = 0
    likes: int = 0
    size_bytes: int = 0  # approx bytes kodo would pull (preferred quant); 0 = unknown


class CuratedModel(BaseModel):
    """A curated starter model offered by `kodo init`."""

    model_config = ConfigDict(frozen=True)

    id: str
    note: str


class CuratedMcp(BaseModel):
    """A curated MCP tool server offered by `kodo mcp list` / added by `kodo mcp add`."""

    model_config = ConfigDict(frozen=True)

    name: str  # tool namespace + kodo.toml [[mcp]].name, e.g. "dhis2"
    command: str  # the kodo.toml [[mcp]].command to run it
    description: str
    setup: str = ""  # one-line hint when the server needs config (a profile, path, key, Node, …)


class ErrorResponse(BaseModel):
    """Standard error response."""

    detail: str
    code: str = "UNKNOWN_ERROR"

"""Detect per-model capabilities (vision, tool calling, context length).

These are *hints* for the UI's model picker, read statically from the weights on
disk without loading the model:

* **vision** — the model has a multimodal projector (GGUF ``mmproj``) or a vision
  config / VL name (MLX / safetensors).
* **tools** — the model's chat template references tool calling. This is a
  heuristic: llama.cpp can drive tools generically, but a template that mentions
  tools is the honest static signal that the model was trained for them.
* **context_length** — the model's trained context window, used as the ceiling
  for the context-length control.

GGUF metadata is read with a minimal header parser (the KV block sits at the
start of the file, so only a small prefix is read). MLX / safetensors read the
sidecar ``config.json`` / ``tokenizer_config.json``.
"""

import json
import struct
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from kodo.library import LibraryModel
from kodo.models import ModelFormat

# Cached detection result, kept beside the model so a scan doesn't re-parse the GGUF header every
# time (detection reads the file's KV block off disk — hundreds of ms for a big model). Lives in
# the ``.kodo`` sidecar, so it travels with the library; the model files are immutable once pulled,
# so the cache never goes stale. A separate file from ``metadata.json`` to stay clear of ``verify``.
_CAPS_CACHE = "capabilities.json"

# GGUF metadata value type codes (ggml spec).
_GGUF_STRING = 8
_GGUF_ARRAY = 9
# Fixed-width scalar type → struct size in bytes.
_GGUF_SCALAR_SIZE = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}

# Chat-template markers that indicate the model was trained for tool calling. Require a
# tool-*calling* structure (``tool_call``/``function_call``/``available_tools``), not a bare
# mention of "tools": some non-tool models (e.g. audio specialists like Ultravox/Voxtral)
# reference "tools" in passing, which falsely marked them tools-capable — after which they'd
# refuse a request citing missing tools. Every genuine tool model here also carries
# ``tool_call``/``tool_calls``, so dropping the loose marker loses no real detections.
_TOOL_MARKERS = ("tool_call", "tool_calls", "function_call", "available_tools")


class ModelCapabilities(BaseModel):
    """Static capability hints for one model."""

    vision: bool = False
    audio: bool = False
    tools: bool = False
    context_length: int | None = None


# Cap a single GGUF metadata string read: chat templates are large but bounded, so a bit-flipped
# uint64 length (corruption on the no-journal exFAT drive) can't slurp gigabytes into RAM. A
# misaligned read past this just yields garbage the (guarded) capability parse discards.
_MAX_GGUF_STRING = 16 * 1024 * 1024


def _read_gguf_string(fh: Any) -> str:
    """Read a GGUF string (uint64 length + UTF-8 bytes) from an open file (length-capped)."""
    (length,) = struct.unpack("<Q", fh.read(8))
    return bytes(fh.read(min(length, _MAX_GGUF_STRING))).decode("utf-8", errors="replace")


def _skip_gguf_value(fh: Any, vtype: int) -> None:
    """Advance past a GGUF value of ``vtype`` we don't need to keep."""
    if vtype == _GGUF_STRING:
        (length,) = struct.unpack("<Q", fh.read(8))
        fh.seek(length, 1)
    elif vtype == _GGUF_ARRAY:
        (elem_type,) = struct.unpack("<I", fh.read(4))
        (count,) = struct.unpack("<Q", fh.read(8))
        for _ in range(count):
            _skip_gguf_value(fh, elem_type)
    else:
        fh.seek(_GGUF_SCALAR_SIZE.get(vtype, 0), 1)


def _read_gguf_scalar(fh: Any, vtype: int) -> Any:
    """Read a fixed-width GGUF scalar value of ``vtype``."""
    fmt = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
    code = fmt.get(vtype)
    if code is None:
        return None
    size = _GGUF_SCALAR_SIZE[vtype]
    (value,) = struct.unpack(code, fh.read(size))
    return value


def _gguf_metadata(path: Path, wanted: set[str]) -> dict[str, Any]:
    """Read requested scalar/string metadata keys from a GGUF file.

    Only scalar and string values are captured (arrays are skipped); parsing
    stops once every wanted key is found. Returns ``{}`` on any read/format error.
    """
    out: dict[str, Any] = {}
    try:
        with path.open("rb") as fh:
            if fh.read(4) != b"GGUF":
                return {}
            struct.unpack("<I", fh.read(4))  # version
            struct.unpack("<Q", fh.read(8))  # tensor count
            (kv_count,) = struct.unpack("<Q", fh.read(8))
            for _ in range(kv_count):
                key = _read_gguf_string(fh)
                (vtype,) = struct.unpack("<I", fh.read(4))
                if key in wanted and vtype == _GGUF_STRING:
                    out[key] = _read_gguf_string(fh)
                elif key in wanted and vtype in _GGUF_SCALAR_SIZE:
                    out[key] = _read_gguf_scalar(fh, vtype)
                else:
                    _skip_gguf_value(fh, vtype)
                if wanted <= out.keys():
                    break
    except (OSError, struct.error, ValueError):
        return out
    return out


def _has_tool_markers(template: str | None) -> bool:
    """Whether a chat template string references tool calling."""
    if not template:
        return False
    low = template.lower()
    return any(marker in low for marker in _TOOL_MARKERS)


def _gguf_capabilities(model: LibraryModel) -> ModelCapabilities:
    """Read capabilities from a GGUF file's metadata."""
    meta = _gguf_metadata(model.load_target, {"general.architecture", "tokenizer.chat_template"})
    arch = meta.get("general.architecture")
    ctx_meta = _gguf_metadata(model.load_target, {f"{arch}.context_length"}) if arch else {}
    ctx = ctx_meta.get(f"{arch}.context_length")

    # An mmproj projector is either a vision or an audio encoder (or both); read
    # its ``clip.has_*_encoder`` flags to tell them apart. Older vision-only
    # projectors predate the flags → treat a bare mmproj as vision.
    vision = False
    audio = False
    if model.mmproj is not None:
        mm = _gguf_metadata(model.mmproj, {"clip.has_vision_encoder", "clip.has_audio_encoder"})
        vision = bool(mm.get("clip.has_vision_encoder"))
        audio = bool(mm.get("clip.has_audio_encoder"))
        if not vision and not audio:
            vision = True

    return ModelCapabilities(
        vision=vision,
        audio=audio,
        tools=_has_tool_markers(meta.get("tokenizer.chat_template")),
        context_length=int(ctx) if isinstance(ctx, int) else None,
    )


def _read_json(path: Path) -> dict[str, Any]:
    """Best-effort JSON object read; ``{}`` on any error."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _dir_chat_template(model_dir: Path, tok: dict[str, Any]) -> str | None:
    """The model's chat template, from tokenizer_config or a sidecar file.

    Newer MLX / HF layouts keep the template in a standalone ``chat_template.jinja``
    (or ``chat_template.json``) rather than inline in ``tokenizer_config.json`` — so
    reading only the latter misses tool markers on those models (a false negative).
    """
    template = tok.get("chat_template")
    if isinstance(template, str) and template:
        return template
    if template:  # non-str (e.g. a list of named templates) → serialize for marker search
        return json.dumps(template)
    for fname in ("chat_template.jinja", "chat_template.json"):
        path = model_dir / fname
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if fname.endswith(".json"):
            data = _read_json(path)
            inner = data.get("chat_template")
            return inner if isinstance(inner, str) else text
        return text
    return None


def _dir_capabilities(model: LibraryModel) -> ModelCapabilities:
    """Read capabilities from an MLX / safetensors model directory's sidecars."""
    config = _read_json(model.load_target / "config.json")
    tok = _read_json(model.load_target / "tokenizer_config.json")
    architectures = [str(a).lower() for a in (config.get("architectures") or [])]
    name_low = model.name.lower()
    vision = (
        "vision_config" in config
        or any(k in a for a in architectures for k in ("vl", "vision", "llava", "idefics"))
        or any(k in name_low for k in ("-vl", "vision", "llava"))
    )
    audio = "audio_config" in config or "audio_token_id" in config
    template_str = _dir_chat_template(model.load_target, tok)
    # Multimodal configs nest the language settings under ``text_config``.
    tc = config.get("text_config")
    text_config: dict[str, Any] = tc if isinstance(tc, dict) else {}
    ctx = config.get("max_position_embeddings") or text_config.get("max_position_embeddings")
    return ModelCapabilities(
        vision=vision,
        audio=audio,
        tools=_has_tool_markers(template_str),
        context_length=int(ctx) if isinstance(ctx, int) else None,
    )


def _cache_path(model: LibraryModel) -> Path:
    """Path to the model's cached-capabilities sidecar file.

    A directory-based model (gguf/mlx/…) keeps the cache in its own ``.kodo`` sidecar. An Ollama
    model's ``path`` is the manifest *file*, so a ``.kodo`` dir can't be made under it (the mkdir
    fails and every scan re-parses the GGUF blob); route it instead to the browsable
    ``ollama/.library/<safe_name>/`` sidecar that ``pull`` already writes the card + metadata into.
    """
    from kodo.cards import SIDECAR_DIR  # noqa: PLC0415 - avoid an import cycle at module load

    if model.is_ollama:
        # Mirror kodo.sources.ollama._safe_name locally to avoid importing the source here.
        safe_name = model.name.replace(":", "_").replace("/", "_")
        return model.library_root / "ollama" / ".library" / safe_name / _CAPS_CACHE
    return model.path / SIDECAR_DIR / _CAPS_CACHE


def _read_cached(model: LibraryModel) -> ModelCapabilities | None:
    """Return the cached capabilities for ``model``, or None if absent/unreadable."""
    try:
        data = json.loads(_cache_path(model).read_text(encoding="utf-8"))
        return ModelCapabilities.model_validate(data)
    except (OSError, json.JSONDecodeError, ValidationError):
        return None


def _write_cached(model: LibraryModel, caps: ModelCapabilities) -> None:
    """Best-effort backfill of the capabilities cache (silent on a read-only mount)."""
    path = _cache_path(model)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(caps.model_dump_json(indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass  # e.g. a read-only drive — detection still returned; we just don't cache it


def _detect(model: LibraryModel) -> ModelCapabilities:
    """Read capabilities straight from the weights on disk (the uncached path)."""
    try:
        if model.model_format is ModelFormat.gguf:
            return _gguf_capabilities(model)
        if model.model_format in (ModelFormat.mlx, ModelFormat.safetensors):
            return _dir_capabilities(model)
    except Exception:  # noqa: BLE001 - capability detection is best-effort; never break the picker
        pass
    return ModelCapabilities()


def capabilities(model: LibraryModel) -> ModelCapabilities:
    """Static capability hints for ``model`` (safe: never raises, defaults to False).

    Reads a cached result from the ``.kodo`` sidecar when present (so a library scan doesn't
    re-parse the GGUF header every time); on a miss it detects from the weights and backfills the
    cache, so the next scan is instant. The model is immutable once pulled, so the cache is stable.
    """
    cached = _read_cached(model)
    if cached is not None:
        return cached
    caps = _detect(model)
    _write_cached(model, caps)
    return caps

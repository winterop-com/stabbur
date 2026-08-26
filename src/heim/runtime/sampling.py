"""Model-recommended sampling parameters.

LM Studio applies each model's recommended sampling (from its
``generation_config.json``); heim mirrors that so a model behaves the way its
authors intended instead of falling back to a runtime's generic defaults.

Read statically from the model directory. GGUF quant repos usually omit
``generation_config.json`` (llama.cpp then uses its own sensible defaults);
MLX / HF repos typically ship it. Values are *defaults*: an explicit value from
the UI/request always overrides.
"""

import json

from pydantic import BaseModel

from heim.library import LibraryModel

# heim's own sampling defaults, applied when a model ships no recommendation of its own
# (the common GGUF-quant case). These are llama.cpp's long-standing defaults, so GGUF
# behavior is unchanged — but stating them explicitly means every setting has a real value
# heim can *show* and send, instead of silently inheriting whatever runtime happens to serve
# the model (llama.cpp and the MLX servers disagree). An explicit model/request value wins.
DEFAULT_TEMPERATURE = 0.8
DEFAULT_TOP_P = 0.95
DEFAULT_TOP_K = 40
DEFAULT_MIN_P = 0.05
# The one deliberate departure: llama.cpp/mlx default to 1.0 (no penalty), which lets some
# models — especially small roleplay/uncensored ones — degenerate into an endless repeat
# loop. 1.1 is a light, widely-used value that curbs that without changing good output.
DEFAULT_REPEAT_PENALTY = 1.1


def defaults() -> "ModelSampling":
    """Heim's sampling defaults — what a model with no recommendation of its own gets."""
    return ModelSampling(
        temperature=DEFAULT_TEMPERATURE,
        top_p=DEFAULT_TOP_P,
        top_k=DEFAULT_TOP_K,
        min_p=DEFAULT_MIN_P,
        repeat_penalty=DEFAULT_REPEAT_PENALTY,
    )


class ModelSampling(BaseModel):
    """Recommended sampling parameters for a model (all optional)."""

    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    repeat_penalty: float | None = None


def _num(value: object) -> float | None:
    """Coerce a JSON scalar to float, or None if it isn't a real number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def recommended(model: LibraryModel) -> ModelSampling:
    """Read a model's recommended sampling from ``generation_config.json``.

    Every field always comes back set: a value the model recommends, else heim's own
    default (see :func:`defaults`). Callers can therefore show the exact value that will
    be sent, and behavior no longer depends on which runtime serves the model.
    """
    fallback = defaults()
    model_dir = model.load_target if model.load_target.is_dir() else model.load_target.parent
    config_path = model_dir / "generation_config.json"
    try:
        data = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError):
        return fallback
    if not isinstance(data, dict):
        return fallback

    temp = _num(data.get("temperature"))
    top_p = _num(data.get("top_p"))
    top_k_raw = _num(data.get("top_k"))
    min_p = _num(data.get("min_p"))
    # HF calls it repetition_penalty; llama.cpp/mlx call it repeat_penalty. Distinguish "key
    # present" from "absent" so an explicit repeat_penalty: 1.0 (I want no penalty) is respected,
    # not silently bumped to the anti-loop default.
    raw_repeat = data.get("repeat_penalty")
    if raw_repeat is None:
        raw_repeat = data.get("repetition_penalty")
    repeat = _num(raw_repeat)
    return ModelSampling(
        # Some configs ship temperature: 0 / top_p: 1 as "unset" sentinels — ignore
        # those so we don't force greedy decoding on a model that didn't mean to.
        temperature=temp if temp else DEFAULT_TEMPERATURE,
        top_p=top_p if top_p and top_p < 1.0 else DEFAULT_TOP_P,
        top_k=int(top_k_raw) if top_k_raw and top_k_raw > 0 else DEFAULT_TOP_K,
        min_p=min_p if min_p else DEFAULT_MIN_P,
        # Respect an explicit model value (including 1.0 = no penalty); default only when absent/invalid.
        repeat_penalty=repeat if (repeat is not None and repeat > 0) else DEFAULT_REPEAT_PENALTY,
    )

"""Model-recommended sampling parameters.

LM Studio applies each model's recommended sampling (from its
``generation_config.json``); kodo mirrors that so a model behaves the way its
authors intended instead of falling back to a runtime's generic defaults.

Read statically from the model directory. GGUF quant repos usually omit
``generation_config.json`` (llama.cpp then uses its own sensible defaults);
MLX / HF repos typically ship it. Values are *defaults*: an explicit value from
the UI/request always overrides.
"""

import json

from pydantic import BaseModel

from kodo.library import LibraryModel

# A mild repetition penalty applied when a model ships no recommendation of its own
# (the common GGUF-quant case): llama.cpp/mlx default to 1.0 (no penalty), which lets
# some models — especially small roleplay/uncensored ones — degenerate into an
# endless repeat loop. 1.1 is a light, widely-used default that curbs that without
# noticeably changing well-behaved output. An explicit model/request value wins.
DEFAULT_REPEAT_PENALTY = 1.1


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

    When the file is absent or unreadable (the common GGUF-quant case), only the
    mild anti-loop ``repeat_penalty`` default is set; everything else is left to the
    runtime's own defaults.
    """
    fallback = ModelSampling(repeat_penalty=DEFAULT_REPEAT_PENALTY)
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
    # HF calls it repetition_penalty; llama.cpp/mlx call it repeat_penalty.
    repeat = _num(data.get("repeat_penalty")) or _num(data.get("repetition_penalty"))
    return ModelSampling(
        # Some configs ship temperature: 0 / top_p: 1 as "unset" sentinels — ignore
        # those so we don't force greedy decoding on a model that didn't mean to.
        temperature=temp if temp else None,
        top_p=top_p if top_p and top_p < 1.0 else None,
        top_k=int(top_k_raw) if top_k_raw and top_k_raw > 0 else None,
        min_p=min_p if min_p else None,
        # Respect an explicit model value; otherwise apply a mild anti-loop default.
        repeat_penalty=repeat if repeat and repeat != 1.0 else DEFAULT_REPEAT_PENALTY,
    )

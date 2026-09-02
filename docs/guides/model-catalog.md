# Model catalog (validated)

A running list of models validated with stabbur — **copy-paste the `sb library pull`
commands** to rebuild a library on a new drive or laptop. Everything the library holds is
just files under `STABBUR_LIBRARY_ROOT`, so "rebuild" = set that root, then run these pulls.
This page grows as models are validated; if a model isn't here, it hasn't been confirmed
working (not that it can't).

!!! tip "Rebuild in one shot"
    The sets below this page describes are also **in stabbur**, so you don't have to paste
    anything:
    ```bash
    export STABBUR_LIBRARY_ROOT=/Volumes/LLM/Library   # your drive
    sb library sets                 # what's on offer
    sb library sync starter --dry-run   # see the plan
    sb library sync starter         # pull what's missing (already-present models are skipped)
    ```
    `sb setup` pulls the starting set itself on a fresh machine (`--no-download` to skip).
    The individual commands below stay for anything outside a set.

## Chat / LLM models

Pulled from the Hugging Face Hub (stabbur picks a balanced GGUF quant, or the MLX build for
`mlx-community` repos). MLX builds are Apple-Silicon-only; GGUF runs everywhere via llama.cpp.

!!! tip "One quant, not the whole ladder"
    A GGUF repo often ships every quant from IQ3 to Q8. A pull with no `--include` takes **one** of
    them — the best available of `Q4_K_M`, `Q4_K_S`, `Q5_K_M`, `Q4_0`, `Q8_0`, plus the `mmproj`
    projector for a vision model. Pass `--include '*Q8_0*'` for a different one, or `--include '*'`
    for everything.

```bash
# --- small (fast, run alongside a voice model) ---
sb library pull huggingface unsloth/Qwen3.5-4B-GGUF --include '*Q4_K_M*'   # 2.6 GB · tool-capable
sb library pull huggingface lmstudio-community/Qwen3.5-4B-MLX-4bit # 2.9 GB · MLX
sb library pull huggingface unsloth/Llama-3.2-3B-Instruct-GGUF    # ~2 GB · tiny starter

# --- mid (capable all-rounders) ---
sb library pull huggingface lmstudio-community/gemma-4-12B-it-QAT-GGUF  # 6.7 GB · tools+vision+audio
sb library pull huggingface deepreinforce-ai/Ornith-1.0-9B-GGUF        # 5.2 GB
sb library pull huggingface TheDrummer/Rocinante-X-12B-v1-GGUF         # 7.0 GB · roleplay
sb library pull huggingface unsloth/gpt-oss-20b-GGUF                   # 10.8 GB · strong reasoning + tools

# --- large (big-context / coding / vision) ---
sb library pull huggingface unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF  # 17.3 GB · coding
sb library pull huggingface lmstudio-community/Qwen3.6-27B-GGUF        # 16.3 GB
sb library pull huggingface mlx-community/Qwen3.6-27B-4bit             # 15.0 GB · MLX
sb library pull huggingface lmstudio-community/gemma-4-31B-it-QAT-GGUF # 17.6 GB · vision
sb library pull huggingface lmstudio-community/gemma-4-26B-A4B-it-QAT-MLX-4bit # 14.6 GB · MLX vision
sb library pull huggingface TheDrummer/Cydonia-24B-v4.3-GGUF          # 13.3 GB
sb library pull huggingface mradermacher/MN-Violet-Lotus-12B-GGUF     # 12.1 GB
```

## Voice models

`sb library pull voice <id>` (downloads, or fast-copies from another reachable library).
TTS speaks; STT transcribes. Except `kokoro` (ONNX, cross-platform), these run on
**mlx-audio (Apple Silicon)**.

```bash
# --- TTS ---
sb library pull voice kokoro       # Kokoro-82M · 54 named voices · the in-chat voice · cross-platform
sb library pull voice voxcpm2      # 2B · 48 kHz · 30 languages · voice design + cloning

# --- STT (speech-to-text) ---
sb library pull voice whisper        # large-v3-turbo · multilingual · the default
sb library pull voice parakeet       # 0.6B · fast · English + 25 EU languages
sb library pull voice qwen3-asr      # 1.7B · multilingual
sb library pull voice distil-whisper # faster distilled Whisper (English)
```

### Voice detail

| id | kind | mode | backend | notes |
| --- | --- | --- | --- | --- |
| `kokoro` | TTS | preset | kokoro-onnx | 54 voices, 8 languages; the in-chat default; cross-platform |
| `voxcpm2` | TTS | design | mlx-audio | 48 kHz, 30 languages; describe the voice you want, or clone from a clip |
| `whisper` | STT | — | mlx-audio | multilingual default |
| `parakeet` | STT | — | mlx-audio | fast; EN + 25 EU languages |
| `qwen3-asr` | STT | — | mlx-audio | multilingual |
| `distil-whisper` | STT | — | mlx-audio | faster English Whisper |

The registry (`stabbur.voice.registry`) is the authoritative list — `sb voice list` prints
what it holds and where each model lives. Models are added to it once they actually run
end-to-end through stabbur's runtime; several TTS checkpoints load but produce no audio via
mlx-audio's high-level API, so they are not listed here. See `ROADMAP.md` in the repo.

## Benchmarks

Tool-calling and code/reasoning are measured by the `sb benchmark` suites (`tools-datetime`,
`tools-utils`, `tools-search`, `tools-web`, `tools-dhis2`, `python`, `rust`). Run `sb benchmark`
to score a model against them; use the results to pick which chat model to bind in a project.

**DHIS2 (`tools-dhis2`, read-only):** the standout is **`deepreinforce-ai/Ornith-1.0-9B-GGUF`** —
a perfect **12/12** driving the DHIS2 CLI bridge, and the **fastest** (~12s/problem) and
**smallest** (5.2 GB) model to do so, beating the 27B/31B models. `gemma-4-12B` and `Qwen3.6-27B`
also went 12/12; `Qwen3.5-4B` (2.6 GB) and `gpt-oss-20b` reached 11/12. Roleplay finetunes
(`Rocinante`, `MN-Violet-Lotus`) scored 0/12 — they never call the tool. See the full write-up in
[DHIS2 benchmark report](dhis2-benchmark-report.md).

**DHIS2 (`tools-dhis2-write`, read-write):** driving **writes** (create / rename / delete against a
local instance) is far harder, and **bigger does not help**. The small **`gemma-4-12B-it-QAT`** leads
at **4/7**; `Qwen3-Coder-30B` and `Qwen3.6-27B` tie at 3/7, `gpt-oss-20b` 2/7, and both `Ornith-1.0-9B`
and the **biggest** model, the 35B-A3B MoE `Qwen3.6-35B-A3B`, trail at 1/7 (the 35B also left the most
residue). The multi-step create→delete→confirm lifecycle trips up every model — the largest ones
over-generate and drop the completion protocol — so no local model is yet trustworthy for unattended
DHIS2 writes.

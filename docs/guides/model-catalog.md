# Model catalog (validated)

A running list of models validated with stabbur — **copy-paste the `stabbur library pull`
commands** to rebuild a library on a new drive or laptop. Everything the library holds is
just files under `STABBUR_LIBRARY_ROOT`, so "rebuild" = set that root, then run these pulls.
This page grows as models are validated; if a model isn't here, it hasn't been confirmed
working (not that it can't).

!!! tip "Rebuild in one shot"
    Set your library, then paste a block below:
    ```bash
    export STABBUR_LIBRARY_ROOT=/Volumes/LLM/Library   # your drive
    # then paste the chat + voice pull commands you want
    ```

## Chat / LLM models

Pulled from the Hugging Face Hub (stabbur picks a balanced GGUF quant, or the MLX build for
`mlx-community` repos). MLX builds are Apple-Silicon-only; GGUF runs everywhere via llama.cpp.

```bash
# --- small (fast, run alongside a voice model) ---
stabbur library pull huggingface unsloth/Qwen3.5-4B-GGUF               # 2.6 GB · tool-capable
stabbur library pull huggingface lmstudio-community/Qwen3.5-4B-MLX-4bit # 2.9 GB · MLX
stabbur library pull huggingface unsloth/Llama-3.2-3B-Instruct-GGUF    # ~2 GB · tiny starter

# --- mid (capable all-rounders) ---
stabbur library pull huggingface lmstudio-community/gemma-4-12B-it-QAT-GGUF  # 6.7 GB · tools+vision+audio
stabbur library pull huggingface deepreinforce-ai/Ornith-1.0-9B-GGUF        # 5.2 GB
stabbur library pull huggingface TheDrummer/Rocinante-X-12B-v1-GGUF         # 7.0 GB · roleplay
stabbur library pull huggingface unsloth/gpt-oss-20b-GGUF                   # 10.8 GB · strong reasoning + tools

# --- large (big-context / coding / vision) ---
stabbur library pull huggingface unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF  # 17.3 GB · coding
stabbur library pull huggingface lmstudio-community/Qwen3.6-27B-GGUF        # 16.3 GB
stabbur library pull huggingface mlx-community/Qwen3.6-27B-4bit             # 15.0 GB · MLX
stabbur library pull huggingface lmstudio-community/gemma-4-31B-it-QAT-GGUF # 17.6 GB · vision
stabbur library pull huggingface lmstudio-community/gemma-4-26B-A4B-it-QAT-MLX-4bit # 14.6 GB · MLX vision
stabbur library pull huggingface TheDrummer/Cydonia-24B-v4.3-GGUF          # 13.3 GB
stabbur library pull huggingface mradermacher/MN-Violet-Lotus-12B-GGUF     # 12.1 GB
```

## Voice models

`stabbur library pull voice <id>` (downloads, or fast-copies from another reachable library).
TTS speaks; STT transcribes. Except `kokoro` (ONNX, cross-platform) and `outetts` (llama.cpp),
these run on **mlx-audio (Apple Silicon)**.

```bash
# --- TTS ---
stabbur library pull voice kokoro       # Kokoro-82M · 54 named voices · the in-chat voice · cross-platform
stabbur library pull voice soprano      # 80M · tiny high-quality English (Kokoro-family)
stabbur library pull voice chatterbox   # expressive · emotion/exaggeration control + cloning
stabbur library pull voice spark        # 0.5B · English + Chinese
stabbur library pull voice csm          # 1B · voice cloning from a reference clip
stabbur library pull voice dia          # 1.6B · nonverbal cues + cloning + multi-speaker (seed it)
stabbur library pull voice outetts      # 500M · GGUF via llama.cpp · cross-platform

# --- STT (speech-to-text) ---
stabbur library pull voice whisper        # large-v3-turbo · multilingual · the default
stabbur library pull voice parakeet       # 0.6B · fast · English + 25 EU languages
stabbur library pull voice qwen3-asr      # 1.7B · multilingual
stabbur library pull voice distil-whisper # faster distilled Whisper (English)
```

### Validated (2026-07-04) — voice detail

| id | kind | mode | backend | notes |
| --- | --- | --- | --- | --- |
| `kokoro` | TTS | preset | kokoro-onnx | 54 voices, 8 languages; the in-chat default; cross-platform |
| `soprano` | TTS | preset | mlx-audio | 80M, tiny English, Kokoro-family |
| `chatterbox` | TTS | preset | mlx-audio | **emotion/exaggeration control** + cloning |
| `spark` | TTS | preset | mlx-audio | English + Chinese (needs `soxr`, in the voice extra) |
| `csm` | TTS | clone | mlx-audio | cloning — pass a reference clip + transcript |
| `dia` | TTS | seeded | mlx-audio | expressive, nonverbal cues, multi-speaker; pin a seed |
| `outetts` | TTS | preset | llama-tts | GGUF; cross-platform |
| `whisper` | STT | — | mlx-audio | multilingual default |
| `parakeet` | STT | — | mlx-audio | fast; EN + 25 EU languages |
| `qwen3-asr` | STT | — | mlx-audio | multilingual |
| `distil-whisper` | STT | — | mlx-audio | faster English Whisper |

**Not yet working** (load but produce no audio via mlx-audio's high-level API, or need bespoke
args): `qwen3-tts`, KittenTTS, OuteTTS-1.0 (mlx), Qwen3-TTS-VoiceDesign, Voxtral-TTS. Tracked in
the roadmap (`ROADMAP.md` in the repo); revisit as mlx-audio adds support.

## Benchmarks

Tool-calling and code/reasoning are measured by the `stabbur benchmark` suites (`tools-datetime`,
`tools-utils`, `tools-search`, `tools-web`, `tools-dhis2`, `python`, `rust`). Run `stabbur benchmark`
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

# Models & compatibility

kodo runs two model families as **external runtime processes** (see
[Running & chatting](running.md)):

- **GGUF → `llama-server`** (llama.cpp) — cross-platform (macOS + Linux). Vision
  and audio come from a paired `--mmproj` projector.
- **MLX → `mlx_lm.server`** (text) / **`mlx_vlm.server`** (vision) — Apple Silicon
  only, faster on the Mac.

kodo detects each model's capabilities (tools · vision · audio · context) from its
metadata and picks the runtime automatically. Detection is a best-effort read of
the files, so it isn't perfect — the matrix below records what's actually been
exercised, and the [known limitations](#known-limitations) call out where reality
diverges from the icons.

## Compatibility matrix

Observed on Apple Silicon, 2026-07-02. **Detected** = the capability icons kodo
shows; **verified** = confirmed working end-to-end in this run. Load times are
first-load and depend heavily on where the model lives (see
[load speed](#load-speed-local-vs-drive)).

| Model | Fmt | Size | Detected | Verified | Load | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| **gemma-4-12B-it-QAT** | gguf | 6.7 GB | tools, vision, audio | chat, reasoning, **vision**, **audio**, tools, TTS | ~2 s (local) | The reference model — everything works. |
| Qwen3.5-4B-MLX-4bit | mlx | 2.9 GB | tools, vision | chat, **tools** | ~45 s | Solid small MLX text model. |
| Qwen3.6-27B-GGUF | gguf | 16.3 GB | tools, vision | loads | minutes (drive) | Large; slow to load from an external drive. |
| MN-Violet-Lotus-12B-GGUF | gguf | 12.1 GB | tools | loads | ~5 s | Roleplay/uncensored — set a system prompt or clear it. |
| Ornith-1.0-9B-GGUF | gguf | 5.2 GB | tools | loads | ~57 s (drive) | Vision **not** detected (the MLX twin does — see below). |
| ultravox-v0_5-llama-3_2-1b-GGUF | gguf | 2.0 GB | tools, audio | loads; **audio fails** | ~2 s | Audio input 500s at the runtime (see limitations). |
| Voxtral-Mini-3B-2507-GGUF | gguf | 3.0 GB | tools, audio | loads; **audio ignored** | ~37 s | Multilingual, but audio input isn't processed. |
| gemma-4-26B-A4B-it-QAT-MLX-4bit | mlx | 14.6 GB | tools, vision, audio | not tested this run | — | Large MLX MoE. |
| Qwen3.6-27B-4bit | mlx | 15.0 GB | vision | not tested this run | — | Tools **not** detected (the GGUF twin does). |
| gemma-4-E4B-it-MLX-4bit | mlx | 6.4 GB | tools, vision, audio | **fails to load** | error ~9 s | Weight mismatch under mlx-vlm. |
| Ornith-1.0-9B-4bit | mlx | 4.1 GB | vision | **fails to load** | error ~2 s | `vision_tower` weight mismatch under mlx-vlm. |

`gemma4:31b` lives in the Ollama store and is **not** runnable by kodo directly
(only via Ollama), so it isn't listed as a runnable model.

## Known limitations

### Dedicated audio models don't process audio yet

**gemma-4-12B handles audio input correctly** (it transcribed a test clip), but the
audio-specialist GGUFs do not, via the current `llama-server`:

- **Ultravox** returns a runtime `500` — `image input is not supported` (its
  audio-only projector is being hit through the image path).
- **Voxtral** silently ignores the audio and answers as a text-only model.

So for **audio input, use a general multimodal model (gemma-4)** for now. The
dedicated audio models load fine but their audio path needs a runtime/projector
fix — tracked as a follow-up.

### Some MLX vision checkpoints fail to load

`gemma-4-E4B-it-MLX-4bit` and `Ornith-1.0-9B-4bit` fail under `mlx_vlm` with a
tensor-key/`vision_tower` mismatch (an upstream mlx-vlm gap for these exports).
kodo **fails fast and surfaces the error** rather than hanging — but the message
is a raw tensor-key dump, which is cryptic. Prefer the **GGUF** builds of these
models. (Ornith GGUF runs; it's Qwen3.5-VL under the hood.)

### Capability detection isn't always consistent

Detection reads model files heuristically, so a model can show different icons
across formats or claim a capability it can't use:

- **Ornith**: the GGUF shows *no vision*, the MLX shows *vision* — same model.
- **Qwen3.6-27B**: the GGUF shows *tools*, the MLX doesn't.
- **Audio specialists** (Ultravox, Voxtral) are marked *tools-capable*, which is
  likely a false positive — attaching tools makes them refuse ("I can't access the
  tools needed") instead of using their native ability. Turn tools off for them.

Treat the icons as a hint, not a guarantee.

### Load speed: local vs drive

Where a model lives dominates load time:

- **`local_root`** (`~/.kodo/library`, on the internal SSD): ~2 s for a 6–7 GB GGUF.
- **`library_root`** (an external USB drive): tens of seconds to minutes — a 5 GB
  model took ~57 s, a 16 GB model minutes. This is drive I/O, not kodo.

**Keep the models you use often on `local_root`** (`kodo pull --local`) for fast
loads and offline use; the big drive is the backup/archive tier. MLX models also
carry a one-time server-startup cost on first load.

## Picking a model

- **General assistant + tools + vision + audio:** `gemma-4-12B-it-QAT-GGUF` — the
  most reliable all-rounder here.
- **Small + fast + tools (Apple Silicon):** `Qwen3.5-4B-MLX-4bit`.
- **Roleplay / uncensored:** `MN-Violet-Lotus-12B-GGUF` — set (or clear) the system
  prompt in the settings rail.
- **Audio input:** use `gemma-4-12B` until the audio-specialist path is fixed.
- **Vision:** `gemma-4-12B` or the Qwen3.x models.

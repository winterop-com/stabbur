# Models & compatibility

stabbur runs two model families as **external runtime processes** (see
[Running & chatting](running.md)):

- **GGUF → `llama-server`** (llama.cpp) — cross-platform (macOS + Linux). Vision
  and audio come from a paired `--mmproj` projector.
- **MLX → `mlx_lm.server`** (text) / **`mlx_vlm.server`** (vision) — Apple Silicon
  only, faster on the Mac.

stabbur detects each model's capabilities (tools · vision · audio · context) from its
metadata and picks the runtime automatically. Detection is a best-effort read of
the files, so it isn't perfect — the matrix below records what's actually been
exercised, and the [known limitations](#known-limitations) call out where reality
diverges from the icons.

## Compatibility matrix

Observed on Apple Silicon, 2026-07-02. **Detected** = the capability icons stabbur
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
| ultravox-v0_5-llama-3_2-1b-GGUF | gguf | 2.0 GB | tools, audio | loads; **audio works** | ~2 s | Transcribes reliably. Regurgitates tool schemas — use `--no-tools`. |
| Voxtral-Mini-3B-2507-GGUF | gguf | 3.0 GB | tools, audio | loads; **audio works** | ~37 s | Multilingual. Deflects instead of transcribing on some turns — see below. |
| gemma-4-26B-A4B-it-QAT-MLX-4bit | mlx | 14.6 GB | tools, vision, audio | not tested this run | — | Large MLX MoE. |
| Qwen3.6-27B-4bit | mlx | 15.0 GB | vision | not tested this run | — | Tools **not** detected (the GGUF twin does). |
| gemma-4-E4B-it-MLX-4bit | mlx | 6.4 GB | tools, vision, audio | **fails to load** | error ~9 s | Weight mismatch under mlx-vlm. |
| Ornith-1.0-9B-4bit | mlx | 4.1 GB | vision | **fails to load** | error ~2 s | `vision_tower` weight mismatch under mlx-vlm. |

`gemma4:31b` lives in the Ollama store and is **not** runnable by stabbur directly
(only via Ollama), so it isn't listed as a runnable model.

## Known limitations

### Audio-specialist models: fixed upstream, but prompt-sensitive

This was previously recorded as broken — Ultravox returning `500 image input is not
supported`, Voxtral silently ignoring audio. **Neither reproduces on current
`llama.cpp`.** Both transcribe correctly, through `llama-server` directly and through
stabbur. The runtime now reports `init_audio` on load for an audio-only projector.

What remains is a model behaviour, not a capability gap, and it is easy to mistake for
one:

- **The prompt matters.** Asking Voxtral to *"Transcribe the audio"* reliably produces a
  refusal — *"I'm unable to transcribe audio directly"* — while *"Repeat exactly what you
  hear"* transcribes. The refusal reads exactly like the audio never arrived.
- **It is not deterministic.** At default sampling Voxtral transcribed 7 of 8 turns; on
  some turns it answers as a generic assistant instead. At `temperature 0` it was 6 of 6.

If you are checking whether a model hears audio, **run the same prompt without the
attachment and compare**. Identical answers mean the audio was ignored; different ones
mean it arrived. Without that control a refusal is indistinguishable from a broken
audio path — which is how this was first recorded as a bug.

gemma-4-12B remains the safest choice for audio: its projector carries both vision and
audio encoders, and it does not deflect.

### Some MLX vision checkpoints fail to load

`gemma-4-E4B-it-MLX-4bit` and `Ornith-1.0-9B-4bit` fail under `mlx_vlm` with a
tensor-key/`vision_tower` mismatch (an upstream mlx-vlm gap for these exports).
stabbur **fails fast and surfaces the error** rather than hanging — but the message
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

- **A fast library** (internal SSD, or a fast external like a Samsung T9): ~2 s for a 6–7 GB GGUF.
- **`library_root`** (an external USB drive): tens of seconds to minutes — a 5 GB
  model took ~57 s, a 16 GB model minutes. This is drive I/O, not stabbur.

**Keep the models you use often in a fast library** (e.g. a project-local `models/`) for fast
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

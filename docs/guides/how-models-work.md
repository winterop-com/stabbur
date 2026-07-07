# How models work: formats & modalities

A technical tour of what the files in your library actually are, why the same
model comes in several formats, and how a "vision" or "audio" model differs from a
plain text one. This is the mental model behind kodo's runtime choices and
capability icons.

## The two-layer picture

Every model is really two things:

1. **Weights** — a big pile of numbers (the learned parameters). How they're
   *stored on disk* is the **format** (safetensors / GGUF / MLX).
2. **A runtime** — a program that loads those weights and runs the math. kodo
   spawns the right one per format (`llama-server`, `mlx_lm.server`, …).

The format and the runtime are paired: GGUF is made for llama.cpp, MLX for Apple's
MLX. Same model, different container + engine.

## Formats

### safetensors — the source weights

The Hugging Face standard. A safe, memory-mappable container for raw tensors,
usually at **full precision** (fp16/bf16 — 2 bytes per parameter). This is the
*original* model as trained: a 12B model is ~24 GB.

- **Used for:** fine-tuning, converting to other formats, and running under
  PyTorch / `transformers` / MLX.
- **In kodo:** kept only when you'll re-quantize or fine-tune — it's the heaviest
  tier and not what you run day-to-day. An MLX repo is technically safetensors too
  (see below).

### GGUF — the portable, quantized backbone

llama.cpp's format: a **single self-contained file** holding the weights *plus* the
tokenizer, metadata, and chat template. It's almost always **quantized** (see
[below](#quantization)), so it's small (a 12B model at Q4 is ~7 GB).

- **Runtime:** `llama-server` (a C++ binary — `brew install llama.cpp`). Runs on
  **CPU and GPU, macOS and Linux** — the most portable tier.
- **Self-contained:** because the chat template ships inside the file, kodo doesn't
  need a separate config to format prompts.
- **Multimodal:** vision/audio come from a **separate `mmproj` file** loaded
  alongside (`--mmproj`), see [modalities](#modalities-in-and-out).
- **In kodo:** the default. The `gguf/` (or `huggingface/`) tree; picked by
  `runtime.build_command` → `llama-server`.

### MLX — Apple Silicon native

Apple's array framework. An MLX repo is **safetensors + a `config.json`**, usually
**quantized to 4-bit**, laid out for MLX's kernels. It uses the Mac's **unified
memory** (CPU and GPU share RAM, no copies), so it's typically the **fastest option
on an M-series Mac**.

- **Runtime:** `mlx_lm.server` (text) or `mlx_vlm.server` (vision/audio) — an
  optional, platform-gated extra (`make install-mlx`). **Apple Silicon only** (no
  Linux wheels).
- **Trade-off:** fastest on the Mac, but not portable, and the vision/audio support
  (mlx-vlm) is younger — some multimodal exports don't load (see
  [Models & compatibility](models.md)).

### At a glance

| | safetensors | GGUF | MLX |
| --- | --- | --- | --- |
| Precision | full (fp16/bf16) | quantized (Q2–Q8) | usually 4-bit |
| Size (12B) | ~24 GB | ~7 GB | ~7 GB |
| Runtime | transformers / MLX | llama.cpp | mlx_lm / mlx-vlm |
| Platforms | any (with a GPU) | **macOS + Linux**, CPU+GPU | **Apple Silicon only** |
| Self-contained | no (needs config) | **yes** | no (needs config) |
| Best for | fine-tune / convert | portable everyday runs | fastest on a Mac |

## Quantization

Full-precision weights are 16 bits each; **quantization** stores them in fewer bits
(8, 5, 4, even 2) — trading a little quality for a big cut in size and memory, which
also speeds up inference (less data to move).

- **GGUF names** encode the scheme: `Q4_K_M` = 4-bit, "K-quant", Medium — a common
  sweet spot. Higher (`Q6_K`, `Q8_0`) = closer to original, bigger; lower (`Q3`,
  `Q2`) = smaller, more degraded.
- **MLX names** say the bit width directly: `4bit`, `8bit`.
- **QAT** (as in `gemma-4-12B-it-QAT`) = *quantization-aware training* — the model
  was trained to quantize well, so its 4-bit build holds up better than a naive one.

Rule of thumb: **Q4_K_M / 4-bit is the default** for running; go higher only if you
have the RAM and want the last few percent of quality.

## Modalities (in and out)

"Multimodal" is about **which senses go in and out**. The base of every model here
is a **text→text** LLM; extra modalities are bolted on as **encoders** (for input)
or handled by a **separate model class** (for audio output).

### Text in → text out

The plain LLM. Everything below is an addition to this core.

### Image in (vision)

An **image encoder** (CLIP/SigLIP-style) turns a picture into a sequence of vectors,
and a small **projector** maps those into the LLM's token space — so the model
"reads" the image alongside your text. It's **input only**: these models describe or
reason about images, they don't *generate* them.

- **GGUF:** the encoder + projector live in the **`mmproj` file**; kodo loads it with
  `--mmproj`. Detected via the projector's `clip.has_vision_encoder` flag.
- **MLX:** a `vision_config` in `config.json`; kodo routes it to `mlx_vlm.server`.

### Audio in (speech understanding / STT)

Same idea with an **audio encoder** (Whisper-style): audio → vectors → projected into
the LLM, so the model can transcribe or reason about what it hears. Also **input
only**, and also carried by the **`mmproj`** in GGUF (`clip.has_audio_encoder`) — a
single projector can hold *both* a vision and an audio encoder (gemma-4's does).

> A model marked "audio" understands audio **input**. That is **not** the same as
> speaking — see below.

### Audio out (text-to-speech)

Making sound is a **different kind of model** entirely, not an LLM: a **TTS** system
turns text into a waveform (an acoustic model + a **vocoder** that renders samples).
kodo runs these as their own engines, separate from the chat model:

- **Kokoro** (ONNX, built in) — 54 built-in voices; the multi-voice engine.
- **OuteTTS** (a GGUF + a WavTokenizer vocoder, via `llama-tts`) — the fallback.

So the flow is asymmetric: **multimodal LLMs are image/audio *in* → text *out*; TTS
is text *in* → audio *out*.** There's no local **text→image** generation in kodo —
that's yet another model class (diffusion), out of scope.

### Putting it together

| Direction | Handled by | In kodo |
| --- | --- | --- |
| text → text | the LLM | any model |
| **image → text** | vision encoder + projector (mmproj / vision_config) | vision models |
| **audio → text** | audio encoder + projector (mmproj / audio_config) | audio models |
| **text → audio** | a separate TTS model (acoustic + vocoder) | Kokoro / OuteTTS |
| text → image | a diffusion model | not supported |

## How kodo decides

`kodo.capabilities` reads each model's files to detect **tools · vision · audio ·
context length**:

- **GGUF:** parse the GGUF metadata header, and read the `mmproj` projector's
  `clip.has_vision_encoder` / `clip.has_audio_encoder` flags.
- **MLX:** read `config.json` for a `vision_config` / `audio_config`.

Those detected capabilities drive the **capability icons** in the picker and the
**runtime choice** (`runtime.build_command`): GGUF → `llama-server` (+ `--mmproj`
when multimodal), text MLX → `mlx_lm.server`, vision/audio MLX → `mlx_vlm.server`.
Detection is heuristic, so it isn't perfect — see
[Models & compatibility](models.md) for where the icons and reality diverge.

## Which format should I keep?

- **Cross-platform / the default:** **GGUF** at Q4_K_M — runs everywhere, small,
  self-contained.
- **Fastest on an Apple Silicon Mac:** **MLX** 4-bit — if you're Mac-only and want
  speed.
- **Only if you'll fine-tune / convert:** **safetensors** — otherwise it's dead
  weight.

kodo's intended library policy is **GGUF + MLX ready-to-run, safetensors on demand**
(per model, not "keep everything"). Keep the models you use often in a fast library
(internal SSD) for fast loads; the big external drive is the archive tier.

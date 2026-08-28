# Voice (text-to-speech & speech-to-text)

Voice models are a first-class category in stabbur, distinct from chat LLMs: their
job is **audio in/out**, not next-token prediction. stabbur can **speak** text
(TTS), **transcribe** speech (STT), and **clone** a voice from a short reference
clip — from the CLI, the web UI, and the OpenAI-compatible API.

## Models

stabbur keeps a small registry of voice models it knows how to run. Add one to your
library — like any model — with `sb library pull voice <id>` (e.g.
`sb library pull voice kokoro`). It lands in the **project-local** library by default
(`--shared` for the archive), and acquires it the cheapest way: if another library in
scope (like `@shared`) already has it, it's **copied** (fast, no download); otherwise
it's pulled from the HF cache or downloaded from Hugging Face:

| Model | Kind | Backend | Notes |
| --- | --- | --- | --- |
| **Kokoro-82M** | TTS | kokoro-onnx | 54 built-in voices, 8 languages. Small + fast — stabbur's **default in-chat voice** (runs next to a big LLM). Cross-platform. |
| **Spark-TTS-0.5B** | TTS | mlx-audio | Bilingual (English + Chinese); pick a gender and **pin a seed** for a stable voice, or **clone** from a reference clip. |
| **Whisper large-v3-turbo** | STT | mlx-audio | Fast multilingual transcription — the mic → prompt side. |
| **Parakeet TDT 0.6B v3** | STT | mlx-audio | Lighter and quicker than Whisper; English + 25 European languages. |
| **Qwen3-ASR-1.7B** | STT | mlx-audio | Multilingual transcription. |
| **Distil-Whisper large-v3** | STT | mlx-audio | A faster distilled Whisper (English only). |

## Backends & install

- **kokoro-onnx** — cross-platform (macOS + Linux) ONNX runtime. Built in (a base
  dependency, no extra to install); espeak-ng is bundled via `espeakng_loader`, so
  there's no system binary to install.
- **mlx-audio** — Apple-Silicon only (everything but Kokoro). A platform-gated extra:
  `uv sync --extra voice` (a no-op off Apple Silicon). English G2P via `misaki[en]`.

On Linux, Kokoro (ONNX) covers TTS; the mlx-audio models are macOS-only.

## CLI

```bash
sb library pull voice kokoro  # add a voice model to the project-local library (downloads if needed)
sb library pull voice kokoro --shared   # ...into the shared/default library instead
sb voice list                 # voice models + where each lives (project libraries + @shared)
sb voice import --all         # back-compat alias: import everything already in the HF cache
sb voice voices               # list Kokoro's 54 named voices
sb voice speak "Hello there"                 # speak with the default engine
sb voice speak "Hi" --voice af_heart         # a specific Kokoro voice
sb voice speak "Hi" --model spark --seed 0   # a seeded model (pin the seed for reliability)
sb voice speak "Hi" --model spark \          # clone the voice in a clip (cloneable models)
  --ref-audio sample.wav --ref-text "exact transcript of sample.wav"
sb voice speak "Hi" --format mp3 -o out.mp3  # export via ffmpeg
```

## Web UI — the Voice studio

`sb serve --ui` has a **Voice** studio (a peer surface to Chat), plus the voice
models listed in the **Library** (under the *Voice* category, alongside *Chat*).
The studio:

- **Text to speech** — pick a model, type (a model-specific sample line prefills),
  choose an output format, and **Generate**. The result plays in an inline player
  (play/pause + scrubber). The controls follow the model: a **seeded** model gets a
  seed field (with a dice to randomize it) and a nonverbal-cue palette, a
  **cloneable** one gets clone-from-clip — upload *or* record a reference clip
  (auto-transcribed by the STT model, silence auto-stops the recorder).
- **Speech to text** — upload or record audio; the STT model returns the transcript.

**In chat:** non-audio models get a **dictation mic** in the composer (Whisper →
prompt), and each reply has a **Listen** button (Kokoro by default — the
lightweight voice never loads a multi-GB model just to speak a reply).

## API (OpenAI-compatible)

Served by `sb serve`, so any OpenAI client works:

```bash
# Text to speech (wav | mp3 | flac | opus | ogg | aac)
curl -X POST localhost:2222/v1/audio/speech -H 'Content-Type: application/json' \
  -d '{"model":"kokoro","input":"Hello","voice":"af_heart","response_format":"mp3"}' -o out.mp3

# Voice cloning (a cloneable model): ref_audio_b64 (base64 WAV) + ref_text
curl -X POST localhost:2222/v1/audio/speech -H 'Content-Type: application/json' \
  -d '{"model":"spark","input":"Cloned line.","ref_audio_b64":"...","ref_text":"...","seed":0}' -o out.wav

# Speech to text
curl -X POST localhost:2222/v1/audio/transcriptions -F model=whisper -F file=@clip.wav
```

Non-WAV formats are transcoded with **ffmpeg** (WAV passes through untouched).

## Notes

- **Target languages: English** for now (`misaki[en]` G2P). Norwegian may follow.
- **A seeded model is stochastic** — its timbre is sampled fresh each run, and an
  unlucky seed can drone instead of speak. The UI defaults to a known-good seed; the
  CLI takes `--seed`. A seed is only reproducible against the installed mlx version:
  an MLX upgrade re-maps seeds to voices, so a seed you liked may not survive one.
- Voice models live in `<library>/voice/…` so they travel with the drive.

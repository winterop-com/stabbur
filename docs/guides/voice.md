# Voice (text-to-speech & speech-to-text)

Voice models are a first-class category in kodo, distinct from chat LLMs: their
job is **audio in/out**, not next-token prediction. kodo can **speak** text
(TTS), **transcribe** speech (STT), and **clone** a voice from a short reference
clip — from the CLI, the web UI, and the OpenAI-compatible API.

## Models

kodo keeps a small registry of voice models it knows how to run. Import them into
your library with `kodo voice import --all` (they're pulled from Hugging Face):

| Model | Kind | Backend | Notes |
| --- | --- | --- | --- |
| **Kokoro-82M** | TTS | kokoro-onnx | 54 built-in voices, 8 languages. Small + fast — kodo's **default in-chat voice** (runs next to a big LLM). Cross-platform. |
| **Dia-1.6B** | TTS | mlx-audio | Expressive; nonverbal cues (`(laughs)`, `(coughs)`) and **voice cloning** from a clip. Its voice is random each run — **pin a seed** for a repeatable, reliable result. |
| **OuteTTS-0.2-500M** | TTS | llama-tts | GGUF TTS via llama.cpp + a vocoder. |
| **Whisper large-v3-turbo** | STT | mlx-audio | Fast multilingual transcription — the mic → prompt side. |
| Qwen3-TTS-0.6B | TTS | mlx-audio | *Not runnable yet* — needs bespoke speech-tokenizer loading mlx-audio's simple loader doesn't do. |

## Backends & install

- **kokoro-onnx** — cross-platform (macOS + Linux) ONNX runtime. Install with
  `make install-tts` (`uv sync --extra tts`). Needs the `espeak-ng` system binary.
- **mlx-audio** — Apple-Silicon only (Dia, Whisper). A platform-gated extra:
  `uv sync --extra voice` (a no-op off Apple Silicon). English G2P via `misaki[en]`.
- **llama-tts** — the `llama-tts` binary from llama.cpp (`brew install llama.cpp`).

On Linux, Kokoro (ONNX) covers TTS; the mlx-audio models are macOS-only.

## CLI

```bash
kodo voice list                 # voice models in the library (TTS + STT)
kodo voice import --all         # import known voice models to the library
kodo voice voices               # list Kokoro's 54 named voices
kodo voice speak "Hello there"                 # speak with the default engine
kodo voice speak "Hi" --voice af_heart         # a specific Kokoro voice
kodo voice speak "Hi" --model dia --seed 0     # Dia (pin a seed for reliability)
kodo voice speak "Hi" --model dia \            # clone the voice in a clip
  --ref-audio sample.wav --ref-text "exact transcript of sample.wav"
kodo voice speak "Hi" --format mp3 -o out.mp3  # export via ffmpeg
```

## Web UI — the Voice studio

`kodo serve --ui` has a **Voice** studio (a peer surface to Chat), plus the voice
models listed in the **Library** (under the *Voice* category, alongside *Chat*).
The studio:

- **Text to speech** — pick a model, type (a model-specific sample line prefills),
  choose an output format, and **Generate**. The result plays in an inline player
  (play/pause + scrubber). For **Dia**: a nonverbal-cue palette, a **seed** field
  (with a dice to randomize it) and **clone-from-clip** — upload *or* record a
  reference clip (auto-transcribed by Whisper, silence auto-stops the recorder).
- **Speech to text** — upload or record audio; Whisper returns the transcript.

**In chat:** non-audio models get a **dictation mic** in the composer (Whisper →
prompt), and each reply has a **Listen** button (Kokoro by default — the
lightweight voice never loads a multi-GB model just to speak a reply).

## API (OpenAI-compatible)

Served by `kodo serve`, so any OpenAI client works:

```bash
# Text to speech (wav | mp3 | flac | opus | ogg | aac)
curl -X POST localhost:8000/v1/audio/speech -H 'Content-Type: application/json' \
  -d '{"model":"kokoro","input":"Hello","voice":"af_heart","response_format":"mp3"}' -o out.mp3

# Voice cloning (Dia): ref_audio_b64 (base64 WAV) + ref_text
curl -X POST localhost:8000/v1/audio/speech -H 'Content-Type: application/json' \
  -d '{"model":"dia","input":"Cloned line.","ref_audio_b64":"...","ref_text":"...","seed":0}' -o out.wav

# Speech to text
curl -X POST localhost:8000/v1/audio/transcriptions -F model=whisper -F file=@clip.wav
```

Non-WAV formats are transcoded with **ffmpeg** (WAV passes through untouched).

## Notes

- **Target languages: English** for now (`misaki[en]` G2P). Norwegian may follow.
- **Dia is stochastic** — an unlucky seed can drone instead of speak. The UI
  defaults to a known-good seed; the CLI takes `--seed`. Plain text is more
  reliable than `[S1]/[S2]` speaker tags in mlx-audio's Dia.
- Voice models live in `<library>/voice/…` so they travel with the drive.

import { useEffect, useMemo, useRef, useState } from "react";
import { AudioLines, Loader2, Mic, Play, Sparkles, Upload, Users, Wand2 } from "lucide-react";

import {
  getVoiceModels,
  getVoices,
  synthesizeSpeech,
  transcribeAudio,
  type Voice,
  type VoiceModelInfo,
} from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** Output formats offered in the playground (WAV always; the rest need ffmpeg). */
const FORMATS = ["wav", "mp3", "opus", "flac"] as const;

/** Dia nonverbal cues, inserted into the dialogue at a click. */
const NONVERBALS = ["(laughs)", "(sighs)", "(coughs)", "(gasps)", "(clears throat)", "(whispers)"];

const BACKEND_LABEL: Record<string, string> = {
  "kokoro-onnx": "Kokoro (ONNX)",
  "mlx-audio": "mlx-audio",
  "llama-tts": "llama-tts",
};

function shortName(name: string): string {
  return name.split("/").pop() ?? name;
}

/** Read a File/Blob as a base64 string (no data: prefix). */
function toBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const url = String(reader.result);
      resolve(url.slice(url.indexOf(",") + 1)); // strip "data:...;base64,"
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

/** The registry id kodo uses for a voice model (so the endpoint can resolve its backend). */
function voiceId(m: VoiceModelInfo): string {
  const n = shortName(m.name).toLowerCase();
  if (n.includes("kokoro")) return "kokoro";
  if (n.includes("dia")) return "dia";
  if (n.includes("qwen3-tts")) return "qwen3-tts";
  if (n.includes("oute")) return "outetts";
  if (n.includes("whisper")) return "whisper";
  return m.name; // fall back to the repo; the endpoint resolves by_repo too
}

/** A read-only reference card for one voice model. */
function VoiceCard({ model }: { model: VoiceModelInfo }) {
  return (
    <div className="flex flex-col rounded-xl border border-border p-3 transition-colors hover:border-primary/40">
      <div className="flex items-center justify-between gap-2">
        <span
          className={cn(
            "rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
            model.kind === "tts"
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
              : "border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400",
          )}
        >
          {model.kind}
        </span>
        <span className="text-xs text-muted-foreground">{model.size_human}</span>
      </div>
      <div className="mt-2 break-words text-sm font-medium leading-snug" title={model.name}>
        {model.display_name || shortName(model.name)}
      </div>
      <div className="truncate text-[11px] text-muted-foreground">{BACKEND_LABEL[model.backend] ?? model.backend}</div>
      {model.description && (
        <p className="mt-1.5 line-clamp-3 text-[11px] leading-relaxed text-muted-foreground">{model.description}</p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
        {model.chat_default && (
          <span className="inline-flex items-center gap-1 rounded-full border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-amber-600 dark:text-amber-400">
            <Sparkles className="h-2.5 w-2.5" /> chat voice
          </span>
        )}
        {model.cloneable && (
          <span className="inline-flex items-center gap-1 rounded-full border border-border px-1.5 py-0.5">
            <Wand2 className="h-2.5 w-2.5" /> clone
          </span>
        )}
        {model.multi_speaker && (
          <span className="inline-flex items-center gap-1 rounded-full border border-border px-1.5 py-0.5">
            <Users className="h-2.5 w-2.5" /> dialogue
          </span>
        )}
        {model.seeded && <span className="rounded-full border border-border px-1.5 py-0.5">seeded</span>}
        {model.languages.length > 0 && (
          <span className="rounded-full border border-border px-1.5 py-0.5">{model.languages.join(" ")}</span>
        )}
      </div>
    </div>
  );
}

/** The text-to-speech playground: pick a model, drive its voice, synthesize + play. */
function SpeakPanel({ ttsModels, kokoroVoices }: { ttsModels: VoiceModelInfo[]; kokoroVoices: Voice[] }) {
  const [modelName, setModelName] = useState<string>(() => ttsModels[0]?.name ?? "");
  const model = useMemo(() => ttsModels.find((m) => m.name === modelName), [ttsModels, modelName]);
  const [text, setText] = useState("Hello from kodo. This voice runs fully on your own machine.");
  const [voice, setVoice] = useState<string>("af_heart");
  const [format, setFormat] = useState<string>("wav");
  const [seed, setSeed] = useState<string>("");
  const [refText, setRefText] = useState("");
  const [refB64, setRefB64] = useState<string | null>(null);
  const [refName, setRefName] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const dialogueRef = useRef<HTMLTextAreaElement>(null);

  const isKokoro = model?.backend === "kokoro-onnx" || voiceId(model ?? ({} as VoiceModelInfo)) === "kokoro";
  const isDialogue = !!model?.multi_speaker;

  // Keep the model selection valid as the library loads.
  useEffect(() => {
    if (!modelName && ttsModels[0]) setModelName(ttsModels[0].name);
  }, [ttsModels, modelName]);

  const insertCue = (cue: string) => {
    setText((t) => (t.endsWith(" ") || t === "" ? t + cue + " " : t + " " + cue + " "));
    dialogueRef.current?.focus();
  };

  const onPickClip = async (file: File) => {
    setRefB64(await toBase64(file));
    setRefName(file.name);
  };

  const speak = async () => {
    if (!model || !text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const blob = await synthesizeSpeech({
        model: voiceId(model),
        input: text,
        voice: isKokoro ? voice : undefined,
        responseFormat: format,
        refAudioB64: refB64 ?? undefined,
        refText: refB64 ? refText : undefined,
        seed: seed.trim() ? Number(seed) : undefined,
      });
      setAudioUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return URL.createObjectURL(blob);
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (ttsModels.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-muted/40 px-4 py-6 text-sm text-muted-foreground">
        No TTS models in the library. Import one with <code className="font-mono">kodo voice import</code>.
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-border p-4">
      <div className="mb-3 flex items-center gap-2">
        <AudioLines className="h-4 w-4 text-emerald-500" />
        <h3 className="text-sm font-semibold">Text to speech</h3>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <label className="text-[11px] text-muted-foreground">
          Model
          <select
            aria-label="Voice model"
            value={modelName}
            onChange={(e) => setModelName(e.target.value)}
            className="ml-2 h-8 rounded-md border border-border bg-background px-2 text-sm"
          >
            {ttsModels.map((m) => (
              <option key={m.name} value={m.name}>
                {m.display_name || shortName(m.name)}
              </option>
            ))}
          </select>
        </label>

        {isKokoro && kokoroVoices.length > 0 && (
          <label className="text-[11px] text-muted-foreground">
            Voice
            <select
              aria-label="Kokoro voice"
              value={voice}
              onChange={(e) => setVoice(e.target.value)}
              className="ml-2 h-8 rounded-md border border-border bg-background px-2 text-sm"
            >
              {kokoroVoices.map((v) => (
                <option key={v.id} value={v.id.replace(/^kokoro:/, "")}>
                  {v.label}
                  {v.gender ? ` · ${v.gender}` : ""}
                </option>
              ))}
            </select>
          </label>
        )}

        <label className="text-[11px] text-muted-foreground">
          Format
          <select
            aria-label="Output format"
            value={format}
            onChange={(e) => setFormat(e.target.value)}
            className="ml-2 h-8 rounded-md border border-border bg-background px-2 text-sm"
          >
            {FORMATS.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </label>

        {model?.seeded && (
          <label className="text-[11px] text-muted-foreground">
            Seed
            <Input
              aria-label="Seed"
              value={seed}
              onChange={(e) => setSeed(e.target.value)}
              placeholder="random"
              className="ml-2 inline-block h-8 w-24"
            />
          </label>
        )}
      </div>

      {isDialogue && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] text-muted-foreground">Speakers: use [S1] / [S2].</span>
          {NONVERBALS.map((n) => (
            <button
              key={n}
              type="button"
              onClick={() => insertCue(n)}
              className="rounded-full border border-dashed border-border px-2 py-0.5 text-[11px] text-muted-foreground hover:border-primary/50 hover:text-foreground"
            >
              {n}
            </button>
          ))}
        </div>
      )}

      <textarea
        ref={dialogueRef}
        aria-label="Text to speak"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={isDialogue ? 4 : 3}
        placeholder={isDialogue ? "[S1] Hello there. [S2] Hi! (laughs)" : "Type something to say…"}
        className="mt-3 w-full resize-y rounded-lg border border-border bg-background p-3 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary"
      />

      {model?.cloneable && (
        <div className="mt-3 rounded-lg border border-border bg-muted/30 p-3">
          <div className="mb-2 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            <Wand2 className="h-3.5 w-3.5" /> Clone a voice (optional)
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs hover:bg-accent/50">
              <Upload className="h-3.5 w-3.5" />
              {refName || "Reference clip"}
              <input
                type="file"
                accept="audio/*"
                className="hidden"
                aria-label="Reference clip"
                onChange={(e) => e.target.files?.[0] && onPickClip(e.target.files[0])}
              />
            </label>
            {refB64 && (
              <button
                type="button"
                onClick={() => {
                  setRefB64(null);
                  setRefName("");
                }}
                className="text-[11px] text-muted-foreground hover:text-destructive"
              >
                clear
              </button>
            )}
          </div>
          {refB64 && (
            <Input
              aria-label="Reference transcript"
              value={refText}
              onChange={(e) => setRefText(e.target.value)}
              placeholder="Exact transcript of the reference clip (needed for a good clone)"
              className="mt-2 h-8 text-sm"
            />
          )}
        </div>
      )}

      <div className="mt-3 flex items-center gap-3">
        <Button onClick={speak} disabled={busy || !text.trim()} className="gap-1.5">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
          {busy ? "Synthesizing…" : "Speak"}
        </Button>
        {error && <span className="text-xs text-destructive">{error}</span>}
      </div>

      {audioUrl && (
        <audio aria-label="Synthesized audio" controls autoPlay src={audioUrl} className="mt-3 w-full" />
      )}
    </div>
  );
}

/** The speech-to-text panel: upload/record audio, transcribe with Whisper. */
function TranscribePanel({ sttModels }: { sttModels: VoiceModelInfo[] }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<string>("");
  const [fileName, setFileName] = useState<string>("");
  const [recording, setRecording] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const model = sttModels[0];

  const run = async (blob: Blob, name: string) => {
    if (!model) return;
    setBusy(true);
    setError(null);
    setTranscript("");
    try {
      setTranscript(await transcribeAudio(blob, voiceId(model), name));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const toggleRecord = async () => {
    if (recording) {
      recorderRef.current?.stop();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      chunksRef.current = [];
      rec.ondataavailable = (e) => e.data.size && chunksRef.current.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        setRecording(false);
        void run(new Blob(chunksRef.current, { type: "audio/webm" }), "recording.webm");
      };
      recorderRef.current = rec;
      rec.start();
      setRecording(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "microphone unavailable");
    }
  };

  if (sttModels.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-muted/40 px-4 py-6 text-sm text-muted-foreground">
        No speech-to-text model in the library. Import Whisper with{" "}
        <code className="font-mono">kodo voice import</code>.
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-border p-4">
      <div className="mb-3 flex items-center gap-2">
        <Mic className="h-4 w-4 text-sky-500" />
        <h3 className="text-sm font-semibold">Speech to text</h3>
        <span className="text-[11px] text-muted-foreground">{model?.display_name}</span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs hover:bg-accent/50">
          <Upload className="h-3.5 w-3.5" />
          {fileName || "Upload audio"}
          <input
            type="file"
            accept="audio/*"
            className="hidden"
            aria-label="Audio to transcribe"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) {
                setFileName(f.name);
                void run(f, f.name);
              }
            }}
          />
        </label>
        <Button
          variant="outline"
          size="sm"
          onClick={toggleRecord}
          className={cn("gap-1.5", recording && "border-destructive text-destructive")}
        >
          <Mic className="h-3.5 w-3.5" />
          {recording ? "Stop" : "Record"}
        </Button>
        {busy && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
      </div>
      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
      <div
        aria-label="Transcript"
        className="mt-3 min-h-16 whitespace-pre-wrap rounded-lg border border-border bg-muted/20 p-3 text-sm"
      >
        {transcript || <span className="text-muted-foreground">The transcript will appear here.</span>}
      </div>
    </div>
  );
}

/**
 * The Voice section: a text-to-speech playground (Kokoro presets, Dia dialogue +
 * cloning + seed), a Whisper transcriber, and reference cards for every voice
 * model in the library. Peer of the Models view.
 */
export function VoiceView() {
  const [models, setModels] = useState<VoiceModelInfo[]>([]);
  const [kokoroVoices, setKokoroVoices] = useState<Voice[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    getVoiceModels()
      .then(setModels)
      .catch(() => {})
      .finally(() => setLoaded(true));
    getVoices()
      .then((vs) => setKokoroVoices(vs.filter((v) => v.engine === "kokoro")))
      .catch(() => {});
  }, []);

  const ttsModels = useMemo(() => models.filter((m) => m.kind === "tts"), [models]);
  const sttModels = useMemo(() => models.filter((m) => m.kind === "stt"), [models]);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-6 py-6">
        <div className="mb-4 flex items-baseline gap-2">
          <h1 className="text-lg font-semibold tracking-tight">Voice</h1>
          {models.length > 0 && <span className="text-sm text-muted-foreground">{models.length} models</span>}
        </div>

        {!loaded ? (
          <div className="flex items-center gap-2 px-1 py-10 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading voice models…
          </div>
        ) : models.length === 0 ? (
          <div className="rounded-lg border border-border bg-muted/40 px-4 py-6 text-sm text-muted-foreground">
            No voice models yet. Import them with <code className="font-mono">kodo voice import --all</code>.
          </div>
        ) : (
          <div className="space-y-6">
            <SpeakPanel ttsModels={ttsModels} kokoroVoices={kokoroVoices} />
            <TranscribePanel sttModels={sttModels} />

            <section>
              <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Voice models
              </div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {models.map((m) => (
                  <VoiceCard key={m.name} model={m} />
                ))}
              </div>
            </section>
          </div>
        )}
      </div>
    </div>
  );
}

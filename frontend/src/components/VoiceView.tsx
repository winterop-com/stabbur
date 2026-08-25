import { useEffect, useMemo, useRef, useState } from "react";
import {
  AudioLines,
  Dices,
  Loader2,
  Mic,
  Pause,
  Play,
  RotateCcw,
  Square,
  Upload,
  Volume2,
  VolumeX,
  Wand2,
} from "lucide-react";

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
import { AudioScrubber } from "@/components/ui/waveform";
import { BarVisualizer } from "@/components/ui/bar-visualizer";
import { audioPeaks } from "@/lib/audio";
import { startRecording, type Recording } from "@/lib/recorder";
import { cn } from "@/lib/utils";

/** Output formats offered in the playground (WAV always; the rest need ffmpeg). */
const FORMATS = ["wav", "mp3", "opus", "flac"] as const;

/** Nonverbal cues (expressive models), inserted into the dialogue at a click. */
const NONVERBALS = ["(laughs)", "(sighs)", "(coughs)", "(gasps)", "(clears throat)", "(whispers)"];

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

/** The registry id heim uses for a voice model (so the endpoint can resolve its backend). */
function voiceId(m: VoiceModelInfo): string {
  const n = shortName(m.name).toLowerCase();
  if (n.includes("kokoro")) return "kokoro";
  if (n.includes("qwen3-tts")) return "qwen3-tts";
  if (n.includes("whisper")) return "whisper";
  return m.name; // fall back to the repo; the endpoint resolves by_repo too
}

/** A default sample line per model — it names the voice so you hear which is which. A
 *  multi-speaker model gets a two-voice exchange to showcase the dialogue tags. */
function defaultTextFor(m: VoiceModelInfo | undefined): string {
  if (m?.multi_speaker)
    return "[S1] Hey there, welcome to heim! (laughs) [S2] Everything you hear runs right here on your own machine.";
  switch (m ? voiceId(m) : "") {
    case "kokoro":
      return "Hi, I'm Kokoro, a small and fast voice running fully on your machine.";
    case "qwen3-tts":
      return "This is Qwen3 TTS, a compact multilingual voice.";
    default:
      return "Hello from heim. This voice runs fully on your own machine.";
  }
}


/** The text-to-speech playground: pick a model, drive its voice, synthesize + play. */
function SpeakPanel({ ttsModels, kokoroVoices }: { ttsModels: VoiceModelInfo[]; kokoroVoices: Voice[] }) {
  const [modelName, setModelName] = useState<string>(() => ttsModels[0]?.name ?? "");
  const model = useMemo(() => ttsModels.find((m) => m.name === modelName), [ttsModels, modelName]);
  const [text, setText] = useState(() => defaultTextFor(ttsModels[0]));
  const [voice, setVoice] = useState<string>("af_heart");
  const [format, setFormat] = useState<string>("wav");
  // A seeded model is stochastic — an unpinned voice varies (and can drone) every run.
  // Default to a pinned seed so it's repeatable out of the box; clear it for a random voice.
  const [seed, setSeed] = useState<string>("10");
  const [refText, setRefText] = useState("");
  const [refB64, setRefB64] = useState<string | null>(null);
  const [refName, setRefName] = useState<string>("");
  const [refBusy, setRefBusy] = useState(false); // auto-transcribing the reference clip
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  // The live blob URL, mirrored in a ref so we revoke the OLD one outside the setState updater.
  // createObjectURL/revokeObjectURL inside an updater run twice under StrictMode (updaters must
  // be pure), leaking a URL each time; keeping the revoke here makes the updaters side-effect-free
  // and lets the unmount cleanup revoke the final clip (F-10).
  const audioUrlRef = useRef<string | null>(null);
  const setClipUrl = (next: string | null) => {
    if (audioUrlRef.current && audioUrlRef.current !== next) URL.revokeObjectURL(audioUrlRef.current);
    audioUrlRef.current = next;
    setAudioUrl(next);
  };
  const [peaks, setPeaks] = useState<number[]>([]); // waveform of the last clip (for the scrubber)
  const dialogueRef = useRef<HTMLTextAreaElement>(null);

  // --- unified inline player: one control that morphs Speak -> Synthesizing -> a
  // play/pause + scrubber + regenerate row (the native <audio> is hidden, driven here).
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [ended, setEnded] = useState(false); // finished: show the "played" waveform + a replay control
  const [cur, setCur] = useState(0);
  const [dur, setDur] = useState(0);
  const [volume, setVolume] = useState(1); // 0..1, persisted onto the <audio> element
  const [muted, setMuted] = useState(false);
  // Keep the hidden <audio> in sync with the volume/mute controls.
  useEffect(() => {
    const a = audioRef.current;
    if (a) {
      a.volume = volume;
      a.muted = muted;
    }
  }, [volume, muted, audioUrl]);
  const toggleMute = () => setMuted((m) => !m);
  const changeVolume = (v: number) => {
    setVolume(v);
    setMuted(v === 0); // dragging to zero mutes; dragging up unmutes
  };
  const togglePlay = () => {
    const a = audioRef.current;
    if (!a) return;
    if (a.paused) {
      // Replay from the start once it's finished (the waveform sits fully "played" at the end).
      if (a.ended || (a.duration && a.currentTime >= a.duration)) a.currentTime = 0;
      void a.play();
    } else a.pause();
  };
  const fmt = (s: number) => (Number.isFinite(s) ? `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}` : "0:00");
  // Reload + play whenever a fresh clip is synthesized (a changed blob src alone won't
  // restart an already-loaded <audio>), so re-synthesizing after a model/voice change
  // actually plays the new audio instead of the stale one.
  useEffect(() => {
    const a = audioRef.current;
    if (a && audioUrl) {
      setEnded(false);
      a.load();
      void a.play().catch(() => {});
    }
  }, [audioUrl]);

  const isKokoro = model?.backend === "kokoro-onnx" || voiceId(model ?? ({} as VoiceModelInfo)) === "kokoro";
  const isDialogue = !!model?.multi_speaker;

  // Keep the model selection valid as the library loads.
  useEffect(() => {
    if (!modelName && ttsModels[0]) setModelName(ttsModels[0].name);
  }, [ttsModels, modelName]);

  // Swap the sample line to the selected model's default — but only while the user hasn't
  // edited it (i.e. it still equals the previous model's default), so edits are never lost.
  const lastDefaultRef = useRef(defaultTextFor(ttsModels[0]));
  useEffect(() => {
    const prevDefault = lastDefaultRef.current; // capture BEFORE updating the ref
    const next = defaultTextFor(model);
    lastDefaultRef.current = next;
    setText((prev) => (prev === prevDefault ? next : prev));
    // Reset the player on a model switch — the last clip belonged to the old model, so the
    // button should read "Generate" (not "Generate again") for the newly selected voice.
    setClipUrl(null);
    setPeaks([]);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model]);

  // Just drop a fresh random seed into the field — the user generates when ready.
  const randomizeSeed = () => setSeed(String(Math.floor(Math.random() * 100000)));

  const insertCue = (cue: string) => {
    setText((t) => (t.endsWith(" ") || t === "" ? t + cue + " " : t + " " + cue + " "));
    dialogueRef.current?.focus();
  };

  // Reference clip (upload or recording): stash it as base64 and auto-fill its transcript with
  // Whisper (editable) so you don't hand-type it like the mlx-audio CLI needs.
  const useClip = async (blob: Blob, name: string) => {
    setRefB64(await toBase64(blob));
    setRefName(name);
    setRefBusy(true);
    try {
      const t = (await transcribeAudio(blob, "whisper", name)).trim();
      if (t) setRefText(t);
    } catch {
      /* Whisper unavailable / failed — fall back to manual entry */
    } finally {
      setRefBusy(false);
    }
  };
  const onPickClip = (file: File) => void useClip(file, file.name);

  // Record your own voice as the reference clip. Uses the shared VAD recorder, so it
  // auto-stops after a short silence (or click Stop).
  const cloneRec = useRef<Recording | null>(null);
  const [cloneRecording, setCloneRecording] = useState(false);
  const [cloneStream, setCloneStream] = useState<MediaStream | null>(null); // shared with the visualizer
  const stopCloneRecording = async () => {
    const rec = cloneRec.current;
    if (!rec) return;
    cloneRec.current = null;
    setCloneRecording(false);
    setCloneStream(null);
    try {
      const wavUrl = await rec.stop();
      await useClip(await (await fetch(wavUrl)).blob(), "recording.wav");
    } catch (e) {
      setError(e instanceof Error ? e.message : "recording failed"); // decode/permission error — surface it
    }
  };
  const toggleCloneRecording = async () => {
    if (cloneRecording) return void stopCloneRecording();
    try {
      cloneRec.current = await startRecording({ onSilence: () => void stopCloneRecording() });
      setCloneStream(cloneRec.current.stream);
      setCloneRecording(true);
    } catch {
      setCloneRecording(false); // permission denied / unsupported
      setCloneStream(null);
    }
  };

  // On unmount (leaving the Voice studio mid-record), cancel any in-flight reference recording so
  // the mic/VAD don't leak (F-2), and revoke the last synthesized clip's blob URL (F-10).
  useEffect(
    () => () => {
      cloneRec.current?.cancel();
      if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
    },
    [],
  );

  const speak = async (seedOverride?: number) => {
    if (!model || !text.trim()) return;
    setBusy(true);
    setError(null);
    audioRef.current?.pause(); // stop the previous clip so it can't play during generation
    try {
      const blob = await synthesizeSpeech({
        model: voiceId(model),
        input: text,
        voice: isKokoro ? voice : undefined,
        responseFormat: format,
        refAudioB64: refB64 ?? undefined,
        refText: refB64 ? refText : undefined,
        // A non-numeric seed field must become "no seed" (undefined), not NaN — NaN serializes
        // to `"seed": null` in JSON, which the server reads as an explicit null, not "unset" (F-12).
        seed: seedOverride ?? (Number.isFinite(Number(seed)) && seed.trim() ? Number(seed) : undefined),
      });
      setClipUrl(URL.createObjectURL(blob));
      setPeaks([]); // clear the old waveform, then fill it once the clip is decoded
      void audioPeaks(blob).then(setPeaks).catch(() => setPeaks([]));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setClipUrl(null); // drop the stale player so a failure isn't masked by old audio
      setPeaks([]);
    } finally {
      setBusy(false);
    }
  };

  const unsupported = model ? !model.supported : false;

  if (ttsModels.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-muted/40 px-4 py-6 text-sm text-muted-foreground">
        No TTS models in the library. Import one with <code className="font-mono">heim voice import</code>.
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
          <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <span>Seed</span>
            <Input
              aria-label="Seed"
              value={seed}
              onChange={(e) => setSeed(e.target.value)}
              placeholder="random"
              className="h-8 w-20"
            />
            <button
              type="button"
              onClick={randomizeSeed}
              aria-label="Randomize seed"
              title="Randomize the seed (then Generate to hear it)"
              className="flex h-8 w-8 items-center justify-center rounded-md border border-border hover:bg-accent/60"
            >
              <Dices className="h-4 w-4" />
            </button>
          </div>
        )}
      </div>

      {isDialogue && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] text-muted-foreground">
            Tip: keep cues mid-line — a trailing one (e.g. ending on "(laughs)") gets clipped.
          </span>
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
        placeholder={isDialogue ? "Say something expressive… add (laughs) or (sighs) for flavor." : "Type something to say…"}
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
              {refName || "Upload clip"}
              <input
                type="file"
                accept="audio/*"
                className="hidden"
                aria-label="Reference clip"
                onChange={(e) => e.target.files?.[0] && onPickClip(e.target.files[0])}
              />
            </label>
            <button
              type="button"
              onClick={toggleCloneRecording}
              aria-label={cloneRecording ? "Stop recording" : "Record reference"}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs hover:bg-accent/50",
                cloneRecording && "border-destructive text-destructive",
              )}
            >
              {cloneRecording ? <Square className="h-3.5 w-3.5" /> : <Mic className="h-3.5 w-3.5" />}
              {cloneRecording ? "Stop" : "Record"}
            </button>
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
          {cloneRecording && (
            <BarVisualizer
              mediaStream={cloneStream}
              barCount={40}
              minHeight={4}
              centerAlign
              className="mt-2 h-14 w-full gap-1 rounded-lg border border-border bg-background px-3 [&>div]:bg-destructive"
            />
          )}
          {refB64 && (
            <div className="mt-2">
              <div className="mb-1 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                {refBusy ? (
                  <>
                    <Loader2 className="h-3 w-3 animate-spin" /> Transcribing the clip with Whisper…
                  </>
                ) : (
                  "Reference transcript — auto-filled by Whisper; edit if it's wrong."
                )}
              </div>
              <Input
                aria-label="Reference transcript"
                value={refText}
                onChange={(e) => setRefText(e.target.value)}
                placeholder="Transcript of the reference clip"
                className="h-8 text-sm"
              />
            </div>
          )}
        </div>
      )}

      <div className="mt-3 flex flex-col gap-3">
        <div className="flex items-center gap-3">
          <Button onClick={() => speak()} disabled={busy || unsupported || !text.trim()} className="gap-1.5">
            {busy ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : audioUrl ? (
              <RotateCcw className="h-4 w-4" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            {busy ? "Generating…" : audioUrl ? "Generate again" : "Generate"}
          </Button>
          {unsupported ? (
            <span className="text-xs text-muted-foreground">
              {model?.display_name} isn't runnable in heim yet.
            </span>
          ) : (
            error && <span className="text-xs text-destructive">{error}</span>
          )}
        </div>

        {/* Inline player for the last clip: play/pause + scrubber + time. The Speak
            button above re-synthesizes the current settings (model/voice/text). */}
        {audioUrl && (
          <div
            className={cn(
              "flex items-center gap-3 rounded-2xl border border-border bg-muted/40 p-2 pr-4",
              busy && "pointer-events-none opacity-50", // generating: don't let the old clip play
            )}
          >
            <button
              type="button"
              onClick={togglePlay}
              disabled={busy}
              aria-label={ended ? "Replay" : playing ? "Pause" : "Play"}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground transition-colors hover:bg-primary/90"
            >
              {ended ? (
                <RotateCcw className="h-4 w-4" />
              ) : playing ? (
                <Pause className="h-4 w-4" />
              ) : (
                <Play className="h-4 w-4 pl-0.5" />
              )}
            </button>
            <div className="relative flex-1 overflow-hidden rounded-lg">
              <AudioScrubber
                data={peaks}
                currentTime={cur}
                duration={dur}
                onSeek={(t) => {
                  const a = audioRef.current;
                  if (a) a.currentTime = t;
                  setEnded(false); // scrubbing moves the playhead off the end
                }}
                height={56}
                barWidth={2}
                barGap={1}
                className="w-full"
              />
            </div>
            <span className="shrink-0 tabular-nums text-[11px] text-muted-foreground">
              {fmt(cur)} / {fmt(dur)}
            </span>
            <div className="flex shrink-0 items-center gap-1.5">
              <button
                type="button"
                onClick={toggleMute}
                disabled={busy}
                aria-label={muted || volume === 0 ? "Unmute" : "Mute"}
                title={muted || volume === 0 ? "Unmute" : "Mute"}
                className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent/60 hover:text-foreground"
              >
                {muted || volume === 0 ? <VolumeX className="h-4 w-4" /> : <Volume2 className="h-4 w-4" />}
              </button>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={muted ? 0 : volume}
                onChange={(e) => changeVolume(Number(e.target.value))}
                aria-label="Volume"
                title="Volume"
                className="h-1 w-20 cursor-pointer accent-primary"
              />
            </div>
          </div>
        )}
      </div>

      <audio
        ref={audioRef}
        aria-label="Synthesized audio"
        src={audioUrl ?? undefined}
        onPlay={() => {
          setPlaying(true);
          setEnded(false);
        }}
        onPause={() => setPlaying(false)}
        onEnded={() => {
          // Leave the playhead at the end so the waveform shows fully "played"; the button
          // becomes a replay control that restarts from 0 on click.
          setPlaying(false);
          setEnded(true);
        }}
        onTimeUpdate={(e) => setCur(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => setDur(e.currentTarget.duration)}
        className="hidden"
      />
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
  const [recStream, setRecStream] = useState<MediaStream | null>(null); // shared with the visualizer
  const recRef = useRef<Recording | null>(null);

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

  // Uses the shared VAD recorder, so it auto-stops after a short silence (or click Stop).
  const stopRec = async () => {
    const rec = recRef.current;
    if (!rec) return;
    recRef.current = null;
    setRecording(false);
    setRecStream(null);
    try {
      const wavUrl = await rec.stop();
      void run(await (await fetch(wavUrl)).blob(), "recording.wav");
    } catch (e) {
      setError(e instanceof Error ? e.message : "recording failed"); // decode/permission error — surface it
    }
  };
  const toggleRecord = async () => {
    if (recording) return void stopRec();
    try {
      recRef.current = await startRecording({ onSilence: () => void stopRec() });
      setRecStream(recRef.current.stream);
      setRecording(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "microphone unavailable");
    }
  };

  // Tear down an in-flight dictation recording if the panel unmounts mid-capture so the
  // mic/VAD don't leak (F-2).
  useEffect(() => () => recRef.current?.cancel(), []);

  if (sttModels.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-muted/40 px-4 py-6 text-sm text-muted-foreground">
        No speech-to-text model in the library. Import Whisper with{" "}
        <code className="font-mono">heim voice import</code>.
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
      {recording && (
        <BarVisualizer
          mediaStream={recStream}
          barCount={40}
          minHeight={4}
          centerAlign
          className="mt-3 h-16 w-full gap-1 rounded-lg border border-border bg-muted/30 px-3 [&>div]:bg-sky-500"
        />
      )}
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
            No voice models yet. Import them with <code className="font-mono">heim voice import --all</code>.
          </div>
        ) : (
          <div className="space-y-6">
            <SpeakPanel ttsModels={ttsModels} kokoroVoices={kokoroVoices} />
            <TranscribePanel sttModels={sttModels} />
            <p className="pt-1 text-[11px] text-muted-foreground">
              Browse every voice model in the{" "}
              <a href="#/library" className="font-medium text-primary hover:underline">
                Library
              </a>
              .
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

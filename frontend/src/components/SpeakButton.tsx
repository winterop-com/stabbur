import { useEffect, useRef, useState } from "react";
import { Loader2, Square, Volume2 } from "lucide-react";

import { speak } from "@/api";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

type State = "idle" | "loading" | "playing";

/**
 * "Listen" control on an assistant reply: synthesizes the text to speech via
 * /api/speak (llama-tts) and plays it. Click again while playing to stop.
 */
export function SpeakButton({ text }: { text: string }) {
  const [state, setState] = useState<State>("idle");
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);

  // Clean up the object URL + audio on unmount.
  useEffect(
    () => () => {
      audioRef.current?.pause();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    },
    [],
  );

  const stop = () => {
    audioRef.current?.pause();
    setState("idle");
  };

  const onClick = async () => {
    if (state === "playing") return stop();
    if (state === "loading") return;
    setState("loading");
    try {
      const blob = await speak(text);
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      const url = URL.createObjectURL(blob);
      urlRef.current = url;
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => setState("idle");
      await audio.play();
      setState("playing");
    } catch {
      setState("idle");
    }
  };

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={onClick}
          className="text-muted-foreground"
          aria-label={state === "playing" ? "Stop" : "Listen"}
        >
          {state === "loading" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : state === "playing" ? (
            <Square className="h-3.5 w-3.5 fill-current" />
          ) : (
            <Volume2 className="h-3.5 w-3.5" />
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{state === "playing" ? "Stop" : state === "loading" ? "Synthesizing…" : "Listen"}</TooltipContent>
    </Tooltip>
  );
}

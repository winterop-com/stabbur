import { CornerDownRight, Settings2 } from "lucide-react";

import type { ToolMarker } from "@/lib/types";
import { cn } from "@/lib/utils";

/** A compact inline chip for a tool call / result within an assistant turn. */
export function ToolMarkerChip({ marker }: { marker: ToolMarker }) {
  const isCall = marker.kind === "call";
  return (
    <div
      className={cn(
        "flex max-w-full items-start gap-1.5 rounded-md border px-2 py-1 font-mono text-xs",
        isCall
          ? "border-primary/30 bg-primary/5 text-primary"
          : "border-border/60 bg-muted/40 text-muted-foreground",
      )}
    >
      {isCall ? (
        <Settings2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      ) : (
        <CornerDownRight className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      )}
      <span className="break-all">{marker.detail}</span>
    </div>
  );
}

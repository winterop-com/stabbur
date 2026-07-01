import { useEffect, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { Settings } from "@/lib/store";

/**
 * Settings dialog. The system prompt is stored app-wide (see lib/store.ts) and
 * prepended as a {role:"system"} message on every /api/chat request.
 */
export function SettingsDialog({
  open,
  onOpenChange,
  settings,
  onSave,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  settings: Settings;
  onSave: (s: Settings) => void;
}) {
  const [systemPrompt, setSystemPrompt] = useState(settings.systemPrompt);
  const [maxTokens, setMaxTokens] = useState<string>(settings.maxTokens != null ? String(settings.maxTokens) : "");

  // Re-sync local form state whenever the dialog is (re)opened.
  useEffect(() => {
    if (open) {
      setSystemPrompt(settings.systemPrompt);
      setMaxTokens(settings.maxTokens != null ? String(settings.maxTokens) : "");
    }
  }, [open, settings]);

  const save = () => {
    const n = parseInt(maxTokens, 10);
    onSave({
      systemPrompt: systemPrompt.trim(),
      maxTokens: Number.isFinite(n) && n > 0 ? n : null,
    });
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>
            The system prompt is applied to every conversation (app-wide) and prepended to each request.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4 py-1">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="system-prompt" className="text-sm font-medium">
              System prompt
            </label>
            <Textarea
              id="system-prompt"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="e.g. You are a helpful assistant."
              className="min-h-32 resize-y"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="max-tokens" className="text-sm font-medium">
              Max tokens <span className="text-muted-foreground">(optional)</span>
            </label>
            <Input
              id="max-tokens"
              type="number"
              min={1}
              value={maxTokens}
              onChange={(e) => setMaxTokens(e.target.value)}
              placeholder="default"
              className="w-40"
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={save}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

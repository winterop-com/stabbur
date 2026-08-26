import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** A byte count as the library talks about disk: whole MB under a gigabyte, one decimal above.
 *
 *  Binary units (the same 1024 base `stabbur library` prints), and a *sum* rather than one file's
 *  size — which is why it lives here rather than beside the row that shows a single model:
 *  the Library heading and the status bar are both summing the same models, and two formatters
 *  would drift into disagreeing about the same number on the same screen. */
export function formatBytes(bytes: number): string {
  const gb = bytes / 1024 ** 3;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${(bytes / 1024 ** 2).toFixed(0)} MB`;
}

/** Decode an audio blob and downsample it to `buckets` normalized peaks (0..1),
 *  suitable for feeding a static waveform (e.g. the AudioScrubber). Falls back to an
 *  empty array on decode failure so callers can render a placeholder instead of crashing. */
export async function audioPeaks(blob: Blob, buckets = 128): Promise<number[]> {
  const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) return [];
  const ctx = new Ctor();
  try {
    const buf = await ctx.decodeAudioData(await blob.arrayBuffer());
    const channel = buf.getChannelData(0);
    const size = Math.max(1, Math.floor(channel.length / buckets));
    const peaks: number[] = [];
    let max = 0;
    for (let i = 0; i < buckets; i++) {
      let sum = 0;
      const start = i * size;
      for (let j = 0; j < size; j++) {
        const v = channel[start + j] ?? 0;
        sum += v * v;
      }
      const rms = Math.sqrt(sum / size);
      peaks.push(rms);
      if (rms > max) max = rms;
    }
    return max > 0 ? peaks.map((p) => p / max) : peaks;
  } catch {
    return [];
  } finally {
    void ctx.close();
  }
}

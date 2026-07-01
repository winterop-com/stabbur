// Record from the microphone and encode to a WAV data URL. The chat runtimes
// accept `input_audio` as wav/mp3 — MediaRecorder produces webm/opus — so we
// decode the recording and re-encode 16-bit PCM WAV client-side.

/** Encode an AudioBuffer (first channel, mono) as a 16-bit PCM WAV Blob. */
function audioBufferToWav(buffer: AudioBuffer): Blob {
  const samples = buffer.getChannelData(0);
  const sampleRate = buffer.sampleRate;
  const bytes = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(bytes);
  const writeStr = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i));
  };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true); // PCM chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeStr(36, "data");
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([view], { type: "audio/wav" });
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result as string);
    r.onerror = reject;
    r.readAsDataURL(blob);
  });
}

/** A live microphone recording; call `stop()` to get a WAV data URL. */
export interface Recording {
  stop: () => Promise<string>;
  cancel: () => void;
}

/** Start recording from the mic. Rejects if permission is denied / unsupported. */
export async function startRecording(): Promise<Recording> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const rec = new MediaRecorder(stream);
  const chunks: Blob[] = [];
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  rec.start();

  const cleanup = () => stream.getTracks().forEach((t) => t.stop());

  return {
    stop: () =>
      new Promise<string>((resolve, reject) => {
        rec.onstop = async () => {
          cleanup();
          try {
            const webm = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
            const ctx = new AudioContext();
            const decoded = await ctx.decodeAudioData(await webm.arrayBuffer());
            await ctx.close();
            resolve(await blobToDataUrl(audioBufferToWav(decoded)));
          } catch (e) {
            reject(e);
          }
        };
        rec.stop();
      }),
    cancel: () => {
      rec.onstop = null;
      try {
        rec.stop();
      } catch {
        /* already stopped */
      }
      cleanup();
    },
  };
}

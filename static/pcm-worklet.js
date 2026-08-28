/**
 * AudioWorklet: resample le micro (sampleRate du contexte, ex. 48kHz)
 * vers 24kHz mono PCM16 — format attendu par l'API Realtime d'OpenAI.
 * Émet des chunks Int16Array (~100 ms) via port.postMessage.
 */
const TARGET_RATE = 24000;
const CHUNK_SAMPLES = 2400; // 100 ms @ 24kHz

class PCMWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this._ratio = sampleRate / TARGET_RATE;
    this._readPos = 0;          // position fractionnaire dans le flux source
    this._pending = new Float32Array(0); // reliquat source non consommé
    this._out = new Int16Array(CHUNK_SAMPLES);
    this._outLen = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch || !ch.length) return true;

    // Concatène reliquat + nouveau bloc
    const src = new Float32Array(this._pending.length + ch.length);
    src.set(this._pending, 0);
    src.set(ch, this._pending.length);

    let pos = this._readPos;
    while (pos + 1 < src.length) {
      // Interpolation linéaire entre les deux échantillons encadrants
      const i = Math.floor(pos);
      const frac = pos - i;
      const sample = src[i] * (1 - frac) + src[i + 1] * frac;
      const s = Math.max(-1, Math.min(1, sample));
      this._out[this._outLen++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this._outLen === CHUNK_SAMPLES) {
        const chunk = this._out.slice(0);
        this.port.postMessage(chunk, [chunk.buffer]);
        this._outLen = 0;
      }
      pos += this._ratio;
    }

    // Garde les échantillons non encore consommés pour le prochain bloc
    const keepFrom = Math.floor(pos);
    this._pending = src.slice(keepFrom);
    this._readPos = pos - keepFrom;
    return true;
  }
}

registerProcessor('pcm-worklet', PCMWorklet);

/** Tiny WebAudio beeps for the scanner flow. Silent (and never throws) where audio is unavailable. */
let ctx: AudioContext | null = null;

function audio(): AudioContext | null {
  if (typeof window === 'undefined') return null;
  const Ctor =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) return null;
  try {
    ctx ??= new Ctor();
    if (ctx.state === 'suspended') void ctx.resume();
    return ctx;
  } catch {
    return null;
  }
}

function tone(freq: number, start: number, duration: number, type: OscillatorType, gain = 0.15) {
  const c = audio();
  if (!c) return;
  try {
    const osc = c.createOscillator();
    const amp = c.createGain();
    osc.type = type;
    osc.frequency.value = freq;
    amp.gain.value = gain;
    osc.connect(amp);
    amp.connect(c.destination);
    const t0 = c.currentTime + start;
    osc.start(t0);
    amp.gain.setValueAtTime(gain, t0 + duration * 0.8);
    amp.gain.linearRampToValueAtTime(0, t0 + duration);
    osc.stop(t0 + duration + 0.02);
  } catch {
    /* ignore audio failures */
  }
}

/** Short high beep: barcode accepted. */
export const beepOk = (): void => tone(1320, 0, 0.09, 'sine');

/** Low double buzz: barcode unknown / rejected. */
export const beepError = (): void => {
  tone(220, 0, 0.18, 'square', 0.12);
  tone(180, 0.22, 0.28, 'square', 0.12);
};

import { useCallback, useEffect, useState } from 'react';

/** Counts down whole seconds from `start()`; `remaining === 0` means ready. */
export function useCountdown() {
  const [remaining, setRemaining] = useState(0);
  useEffect(() => {
    if (remaining <= 0) return;
    const id = setTimeout(() => setRemaining((r) => Math.max(0, r - 1)), 1000);
    return () => clearTimeout(id);
  }, [remaining]);
  const start = useCallback((seconds: number) => setRemaining(Math.max(0, Math.ceil(seconds))), []);
  return { remaining, start };
}

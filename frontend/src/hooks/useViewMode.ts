import { useState } from 'react';

export type ViewMode = 'list' | 'grid';

/** List/card view choice, remembered per browser under `storageKey` (storage may be absent). */
export function useViewMode(storageKey: string): [ViewMode, (v: ViewMode) => void] {
  const [view, setView] = useState<ViewMode>(() => {
    try {
      return localStorage.getItem(storageKey) === 'grid' ? 'grid' : 'list';
    } catch {
      return 'list';
    }
  });
  const set = (v: ViewMode) => {
    setView(v);
    try {
      localStorage.setItem(storageKey, v);
    } catch {
      // storage unavailable (private mode): the choice just is not remembered
    }
  };
  return [view, set];
}

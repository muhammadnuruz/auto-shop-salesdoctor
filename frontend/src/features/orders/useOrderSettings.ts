import { useCallback, useState } from 'react';

export const OPTIONAL_COLUMNS = [
  'photo',
  'category',
  'price',
  'stock',
  'blocks',
  'pieces',
  'sum',
  'comment',
] as const;
export type OptionalColumn = (typeof OPTIONAL_COLUMNS)[number];

export interface OrderSettings {
  columns: Record<OptionalColumn, boolean>;
  sound: boolean;
  autoFocus: boolean;
}

export const SETTINGS_KEY = 'autoshop.order.settings';

export const defaultSettings = (): OrderSettings => ({
  columns: Object.fromEntries(OPTIONAL_COLUMNS.map((c) => [c, true])) as Record<
    OptionalColumn,
    boolean
  >,
  sound: true,
  autoFocus: false,
});

export function loadSettings(): OrderSettings {
  const base = defaultSettings();
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return base;
    const parsed = JSON.parse(raw) as Partial<OrderSettings>;
    for (const c of OPTIONAL_COLUMNS) {
      if (typeof parsed.columns?.[c] === 'boolean') base.columns[c] = parsed.columns[c];
    }
    if (typeof parsed.sound === 'boolean') base.sound = parsed.sound;
    if (typeof parsed.autoFocus === 'boolean') base.autoFocus = parsed.autoFocus;
  } catch {
    /* corrupted or unavailable storage: defaults */
  }
  return base;
}

/** Column visibility, scanner sound and scanner auto-focus, persisted in localStorage. */
export function useOrderSettings() {
  const [settings, setState] = useState<OrderSettings>(loadSettings);
  const update = useCallback((next: OrderSettings) => {
    setState(next);
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(next));
    } catch {
      /* ignore */
    }
  }, []);
  return [settings, update] as const;
}

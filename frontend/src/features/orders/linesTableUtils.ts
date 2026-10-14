import type { KeyboardEvent, RefObject } from 'react';
import type { OrderLine } from '@/api/types';

export const VIRTUALIZE_AFTER = 200;
export const ROW_H = 76;
export const OVERSCAN = 12;
export const CARD_CHUNK = 100;

export function filterLines(lines: OrderLine[], q: string): OrderLine[] {
  const needle = q.trim().toLowerCase();
  if (!needle) return lines;
  return lines.filter(
    (l) =>
      (l.name ?? '').toLowerCase().includes(needle) ||
      (l.code ?? '').toLowerCase().includes(needle) ||
      (l.category_name ?? '').toLowerCase().includes(needle) ||
      l.barcodes.some((b) => b.includes(needle)),
  );
}

/** Enter in a quantity input jumps to the same column of the next row. */
export function navigateDown(e: KeyboardEvent<HTMLElement>, root: RefObject<HTMLElement | null>) {
  if (e.key !== 'Enter') return;
  const el = e.target as HTMLElement;
  const col = el.dataset.navCol;
  const row = el.dataset.navRow;
  if (!col || row === undefined || !root.current) return;
  e.preventDefault();
  const next = root.current.querySelector<HTMLInputElement>(
    `input[data-nav-col="${col}"][data-nav-row="${Number(row) + 1}"]`,
  );
  if (next) next.focus();
  else (el as HTMLInputElement).blur();
}

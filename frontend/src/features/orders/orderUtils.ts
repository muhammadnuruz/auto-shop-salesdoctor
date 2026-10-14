import type { TFunction } from 'i18next';
import { ApiError } from '@/api/http';
import { describeError } from '@/lib/errors';
import type { Order, OrderLine, OrderProblem, OrderState } from '@/api/types';

export const hasPack = (pack: number | null | undefined): pack is number => !!pack && pack > 0;

export const round3 = (n: number): number => Math.round(n * 1000) / 1000;
export const round2 = (n: number): number => Math.round(n * 100) / 100;

export const computeQty = (blocks: number, pieces: number, pack: number | null): number =>
  round3(blocks * (hasPack(pack) ? pack : 0) + pieces);

/** Local, optimistic edit of a line (what the user last asked for). */
export interface LineIntent {
  /** product to show if the line does not exist on the server yet */
  product?: Pick<
    OrderLine,
    | 'product_ref'
    | 'name'
    | 'code'
    | 'category'
    | 'category_name'
    | 'unit'
    | 'image_url'
    | 'thumb_url'
    | 'pack_quantity'
    | 'barcodes'
    | 'price'
    | 'stock'
  >;
  blocks?: number;
  pieces?: number;
  comment?: string | null;
  remove?: boolean;
}

/** Server lines with the user's not-yet-saved edits applied (instant feedback). */
export function applyIntents(
  lines: OrderLine[],
  intents: Record<string, LineIntent>,
  allowOverStock: boolean,
): OrderLine[] {
  const keys = Object.keys(intents);
  if (keys.length === 0) return lines;
  const seen = new Set<string>();
  const out: OrderLine[] = [];
  const merge = (base: OrderLine, it: LineIntent): OrderLine => {
    const blocks = it.blocks ?? base.blocks;
    const pieces = it.pieces ?? base.pieces;
    const quantity = computeQty(blocks, pieces, base.pack_quantity);
    const price = base.price ?? 0;
    const warnings = base.warnings.filter((w) => w !== 'over_stock');
    if (!allowOverStock && base.stock != null && quantity > base.stock) warnings.push('over_stock');
    return {
      ...base,
      blocks,
      pieces,
      quantity,
      line_sum: round2(quantity * price),
      comment: it.comment !== undefined ? it.comment : base.comment,
      warnings,
    };
  };
  for (const line of lines) {
    const it = intents[line.product_ref];
    seen.add(line.product_ref);
    if (!it) out.push(line);
    else if (!it.remove) out.push(merge(line, it));
  }
  for (const ref of keys) {
    const it = intents[ref];
    if (seen.has(ref) || it?.remove || !it?.product) continue;
    const p = it.product;
    out.push(
      merge(
        {
          ...p,
          blocks: 0,
          pieces: 0,
          quantity: 0,
          line_sum: 0,
          comment: null,
          position: lines.length + out.length + 1,
          warnings: !p.price ? ['no_price'] : [],
        },
        it,
      ),
    );
  }
  return out;
}

export const isEditableState = (s: OrderState): boolean => s === 'draft' || s === 'failed';

export function stateColor(
  s: OrderState,
): 'default' | 'primary' | 'success' | 'warning' | 'error' | 'info' {
  switch (s) {
    case 'sent':
      return 'success';
    case 'draft':
      return 'default';
    case 'submitting':
    case 'outcome_unknown':
      return 'warning';
    case 'failed':
      return 'error';
    case 'cancelled':
    case 'deleted':
      return 'default';
  }
}

export function sdStatusColor(s: number): 'default' | 'info' | 'success' | 'warning' | 'error' {
  return s === 1 ? 'info' : s === 2 || s === 3 ? 'success' : s === 4 ? 'warning' : 'error';
}

export const canShopCancel = (o: Pick<Order, 'state' | 'sd_status'>): boolean =>
  o.state === 'sent' && o.sd_status === 1;

/** Problems of an ApiError body: `detail.errors` (list) when present. */
export function problemsOf(err: unknown): OrderProblem[] {
  if (!(err instanceof ApiError)) return [];
  const list = err.problem?.errors;
  return Array.isArray(list) ? (list as OrderProblem[]) : [];
}

export function bodyDetail(data: unknown): Record<string, unknown> | null {
  if (data && typeof data === 'object' && 'detail' in data) {
    const d = (data as { detail: unknown }).detail;
    if (d && typeof d === 'object' && !Array.isArray(d)) return d as Record<string, unknown>;
  }
  return null;
}

export function problemText(t: TFunction, p: OrderProblem): string {
  const known = t(`problems.${p.code}`, {
    defaultValue: '',
    available: p.available,
    requested: p.requested,
    min: p.min,
    max: p.max,
    sum: p.sum,
  });
  const text = known || p.message || p.code;
  return p.field ? `${t(`order.field_${p.field}`, { defaultValue: p.field })}: ${text}` : text;
}

/** Group problems: per-line (by product_ref) vs header-level. */
export function splitProblems(problems: OrderProblem[]): {
  header: OrderProblem[];
  byLine: Record<string, OrderProblem[]>;
} {
  const header: OrderProblem[] = [];
  const byLine: Record<string, OrderProblem[]> = {};
  for (const p of problems) {
    if (p.product_ref) (byLine[p.product_ref] ??= []).push(p);
    else header.push(p);
  }
  return { header, byLine };
}

/** Localised message for an order API error (coded problems first, then generic HTTP text). */
export function orderErrorMessage(err: unknown, t: TFunction): string {
  if (err instanceof ApiError) {
    const code = err.problemCode;
    if (code && t(`orderErrors.${code}`, { defaultValue: '' })) {
      return t(`orderErrors.${code}`, { defaultValue: '' });
    }
    const first = problemsOf(err)[0];
    if (first) return problemText(t, first);
    if (err.status === 413) return t('orderErrors.file_too_large');
  }
  return describeError(err, t);
}

/** Locally computed totals (used by the admin editor before the changes are saved). */
export function sumLines(lines: OrderLine[]): { qty: number; sum: number } {
  return {
    qty: round3(lines.reduce((a, l) => a + l.quantity, 0)),
    sum: round2(lines.reduce((a, l) => a + l.line_sum, 0)),
  };
}

export function lineFromCatalog(
  item: Omit<
    OrderLine,
    'blocks' | 'pieces' | 'quantity' | 'line_sum' | 'comment' | 'position' | 'warnings'
  >,
  blocks: number,
  pieces: number,
  position: number,
): OrderLine {
  const quantity = computeQty(blocks, pieces, item.pack_quantity);
  return {
    ...item,
    blocks,
    pieces,
    quantity,
    line_sum: round2(quantity * (item.price ?? 0)),
    comment: null,
    position,
    warnings: !item.price ? ['no_price'] : [],
  };
}

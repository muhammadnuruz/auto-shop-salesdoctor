import type { AdminOrderResponse } from '@/api/admin';
import type { AdminOrder, OrderProblem } from '@/api/types';
import { bodyDetail } from '@/features/orders/orderUtils';

export type AdminOutcome =
  | { kind: 'ok'; order: AdminOrder; pending: boolean; warnings: string[]; reconcile?: string }
  | { kind: 'invalid'; code: string; problems: OrderProblem[]; message?: string }
  | { kind: 'unavailable'; message?: string }
  | { kind: 'unknown'; message?: string }
  | { kind: 'conflict' }
  | { kind: 'forbidden' }
  | { kind: 'state'; code: string }
  | { kind: 'error'; status: number };

const isOrder = (d: unknown): d is AdminOrder =>
  !!d && typeof d === 'object' && 'public_id' in d && 'lines' in d;

/** Maps an admin order action response (200 / 202 / structured 4xx-5xx) onto a UI outcome. */
export function classifyAdmin(res: AdminOrderResponse): AdminOutcome {
  // resend of an outcome_unknown order answers with the order even for non-2xx codes
  if (isOrder(res.data)) {
    return {
      kind: 'ok',
      order: res.data,
      pending: res.status === 202 || res.data.reconcile === 'pending',
      warnings: res.data.warnings ?? [],
      reconcile: res.data.reconcile ?? undefined,
    };
  }
  const detail = bodyDetail(res.data);
  const code = typeof detail?.code === 'string' ? detail.code : '';
  const message = typeof detail?.message === 'string' ? detail.message : undefined;
  const errors = Array.isArray(detail?.errors) ? (detail.errors as OrderProblem[]) : [];
  if (res.status === 202) {
    // status/delete answer 202 {detail:{code:'outcome_unknown'}} without an order body
    return { kind: 'unknown', message };
  }
  if (res.status === 403) return { kind: 'forbidden' };
  if (res.status === 422) return { kind: 'invalid', code, problems: errors, message };
  if (res.status === 502 || res.status === 503) return { kind: 'unavailable', message };
  if (res.status === 409) {
    if (code === 'version_conflict') return { kind: 'conflict' };
    return { kind: 'state', code };
  }
  return { kind: 'error', status: res.status };
}

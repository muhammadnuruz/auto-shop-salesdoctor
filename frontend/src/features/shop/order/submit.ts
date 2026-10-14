import type { Order, OrderProblem } from '@/api/types';
import type { FlowResponse } from '@/api/shop';
import { bodyDetail } from '@/features/orders/orderUtils';

export type SubmitOutcome =
  | { kind: 'sent'; order: Order }
  | { kind: 'unknown'; order: Order }
  | { kind: 'invalid'; problems: OrderProblem[]; message?: string; rejected: boolean }
  | { kind: 'unavailable'; message?: string }
  | { kind: 'busy' }
  | { kind: 'invalid_state'; state?: string }
  | { kind: 'conflict' }
  | { kind: 'error'; status: number };

/** Maps the submit/cancel HTTP response onto what the UI has to do next. */
export function classifySubmit(res: FlowResponse): SubmitOutcome {
  if (res.ok) {
    const order = res.data as Order;
    return res.status === 202 ? { kind: 'unknown', order } : { kind: 'sent', order };
  }
  const detail = bodyDetail(res.data);
  const code = typeof detail?.code === 'string' ? detail.code : null;
  const errors = Array.isArray(detail?.errors) ? (detail.errors as OrderProblem[]) : [];
  const message = typeof detail?.message === 'string' ? detail.message : undefined;
  if (res.status === 422) {
    return { kind: 'invalid', problems: errors, message, rejected: code === 'sd_rejected' };
  }
  if (res.status === 502 || (res.status === 503 && code === 'sd_unavailable')) {
    return { kind: 'unavailable', message };
  }
  if (res.status === 409) {
    if (code === 'order_busy') return { kind: 'busy' };
    if (code === 'version_conflict') return { kind: 'conflict' };
    if (code === 'invalid_state') {
      return {
        kind: 'invalid_state',
        state: typeof detail?.state === 'string' ? detail.state : undefined,
      };
    }
  }
  return { kind: 'error', status: res.status };
}

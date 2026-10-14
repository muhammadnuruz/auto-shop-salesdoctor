import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSnackbar } from 'notistack';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/api/http';
import { qk } from '@/api/keys';
import { ordersApi } from '@/api/shop';
import type { CatalogItem, ImportApplyLine, Order, OrderHeaderPatch, OrderLine } from '@/api/types';
import { applyIntents, orderErrorMessage, type LineIntent } from '@/features/orders/orderUtils';

export const DEBOUNCE_MS = 300;
export const POLL_MS = 3000;

export type ScanResult =
  | { kind: 'ok'; order: Order; ref: string | null }
  | { kind: 'ambiguous'; candidates: CatalogItem[]; code: string }
  | { kind: 'not_found'; code: string; reason: 'barcode_not_found' | 'not_orderable' };

export type SaveState = 'saving' | 'saved' | 'error';

type HeaderIntent = OrderHeaderPatch;

const isConflict = (e: unknown) =>
  e instanceof ApiError && e.status === 409 && e.problemCode === 'version_conflict';

/**
 * Server-side draft autosave. Every edit is applied to the screen immediately (overlay), debounced
 * 300 ms, then sent with the order `version`. Requests are serialised; a 409 version_conflict
 * refetches the order and re-applies the user's latest intent once, otherwise a toast is shown.
 */
export function useShopOrder(orderId: string) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  const key = useMemo(() => qk.shop.orders.detail(orderId), [orderId]);

  const versionRef = useRef(0);
  const query = useQuery({
    queryKey: key,
    queryFn: async ({ signal }) => {
      const o = await ordersApi.get(orderId, signal);
      versionRef.current = o.version;
      return o;
    },
    // an order whose submit result is unknown is polled until SD confirms or rejects it
    refetchInterval: (q) =>
      q.state.data?.state === 'outcome_unknown' || q.state.data?.state === 'submitting'
        ? POLL_MS
        : false,
  });
  const order = query.data;

  const [overlay, setOverlay] = useState<Record<string, LineIntent>>({});
  const [headerOverlay, setHeaderOverlay] = useState<HeaderIntent>({});
  const [inflight, setInflight] = useState(0);
  const [failed, setFailed] = useState(false);
  const pendingRef = useRef<Record<string, LineIntent>>({});
  const headerRef = useRef<HeaderIntent>({});
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const chain = useRef<Promise<unknown>>(Promise.resolve());
  const lastOrder = useRef<Order | undefined>(undefined);
  useEffect(() => {
    lastOrder.current = order;
  });

  const apply = useCallback(
    (o: Order) => {
      versionRef.current = o.version;
      qc.setQueryData(key, o);
    },
    [qc, key],
  );

  const run = useCallback(<T>(fn: () => Promise<T>): Promise<T> => {
    const p = chain.current.then(fn, fn);
    chain.current = p.catch(() => undefined);
    return p;
  }, []);

  const refetch = useCallback(async () => {
    const o = await ordersApi.get(orderId);
    apply(o);
    return o;
  }, [orderId, apply]);

  /** Call with the current version; on version_conflict refetch and retry exactly once. */
  const withRetry = useCallback(
    async (call: (version: number) => Promise<Order>): Promise<Order> => {
      try {
        return await call(versionRef.current);
      } catch (e) {
        if (!isConflict(e)) throw e;
        await refetch();
        return await call(versionRef.current);
      }
    },
    [refetch],
  );

  const flushInner = useCallback(async () => {
    const lines = pendingRef.current;
    const header = headerRef.current;
    pendingRef.current = {};
    headerRef.current = {};
    const refs = Object.keys(lines);
    const headerKeys = Object.keys(header);
    if (refs.length === 0 && headerKeys.length === 0) return;
    setInflight((n) => n + 1);
    try {
      if (headerKeys.length > 0) {
        apply(await withRetry((version) => ordersApi.patchHeader(orderId, { ...header, version })));
      }
      for (const ref of refs) {
        const it = lines[ref];
        if (!it) continue;
        const known = lastOrder.current?.lines.find((l) => l.product_ref === ref);
        if (it.remove) {
          if (known)
            apply(await withRetry((version) => ordersApi.removeLine(orderId, ref, version)));
          continue;
        }
        const blocks = it.blocks ?? known?.blocks ?? 0;
        const pieces = it.pieces ?? known?.pieces ?? 0;
        if (!known && blocks === 0 && pieces === 0) continue; // nothing to create
        const body: { blocks?: number; pieces?: number; comment?: string | null } = {};
        if (it.blocks !== undefined || !known) body.blocks = blocks;
        if (it.pieces !== undefined || !known) body.pieces = pieces;
        if (it.comment !== undefined) body.comment = it.comment;
        apply(
          await withRetry((version) => ordersApi.patchLine(orderId, ref, { ...body, version })),
        );
      }
      setFailed(false);
    } catch (e) {
      setFailed(true);
      enqueueSnackbar(orderErrorMessage(e, t), { variant: 'error' });
      try {
        await refetch(); // resync with the server's truth
      } catch {
        /* the error toast above is enough */
      }
    } finally {
      setInflight((n) => n - 1);
      setOverlay((cur) => {
        const next = { ...cur };
        for (const r of refs) if (!pendingRef.current[r]) delete next[r];
        return next;
      });
      setHeaderOverlay((cur) => {
        const next = { ...cur };
        for (const k of headerKeys) {
          if (!(k in headerRef.current)) delete next[k as keyof HeaderIntent];
        }
        return next;
      });
    }
  }, [apply, withRetry, orderId, refetch, enqueueSnackbar, t]);

  const flush = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    return run(flushInner);
  }, [run, flushInner]);

  const schedule = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      timer.current = null;
      void run(flushInner);
    }, DEBOUNCE_MS);
  }, [run, flushInner]);

  // Don't leave the last edit behind when leaving the screen.
  useEffect(
    () => () => {
      if (timer.current) {
        clearTimeout(timer.current);
        timer.current = null;
        void run(flushInner);
      }
    },
    [run, flushInner],
  );

  const intent = useCallback(
    (ref: string, patch: LineIntent) => {
      const next = { ...pendingRef.current[ref], ...patch };
      if (patch.remove === false) delete next.remove;
      pendingRef.current = { ...pendingRef.current, [ref]: next };
      setOverlay((cur) => ({ ...cur, [ref]: { ...cur[ref], ...next } }));
      schedule();
    },
    [schedule],
  );

  const setQty = useCallback(
    (ref: string, patch: { blocks?: number; pieces?: number }, product?: LineIntent['product']) => {
      const known = lastOrder.current?.lines.find((l) => l.product_ref === ref);
      const cur = { ...overlay[ref], ...pendingRef.current[ref] };
      const blocks = patch.blocks ?? cur.blocks ?? known?.blocks ?? 0;
      const pieces = patch.pieces ?? cur.pieces ?? known?.pieces ?? 0;
      intent(ref, {
        ...patch,
        remove: blocks === 0 && pieces === 0 ? true : false,
        ...(product ? { product } : {}),
      });
    },
    [intent, overlay],
  );

  const setComment = useCallback(
    (ref: string, comment: string) => intent(ref, { comment }),
    [intent],
  );
  const removeLine = useCallback((ref: string) => intent(ref, { remove: true }), [intent]);

  /** Multi-add from the catalog: quantities add up with what is already ordered. */
  const addItems = useCallback(
    (items: { item: CatalogItem; blocks: number; pieces: number }[]) => {
      for (const { item, blocks, pieces } of items) {
        const known = lastOrder.current?.lines.find((l) => l.product_ref === item.product_ref);
        const cur = { ...overlay[item.product_ref], ...pendingRef.current[item.product_ref] };
        const baseBlocks = cur.remove ? 0 : (cur.blocks ?? known?.blocks ?? 0);
        const basePieces = cur.remove ? 0 : (cur.pieces ?? known?.pieces ?? 0);
        intent(item.product_ref, {
          blocks: baseBlocks + blocks,
          pieces: basePieces + pieces,
          remove: false,
          product: { ...item, price: item.price ?? 0, stock: item.stock },
        });
      }
    },
    [intent, overlay],
  );

  const setHeader = useCallback(
    (patch: OrderHeaderPatch) => {
      headerRef.current = { ...headerRef.current, ...patch };
      setHeaderOverlay((cur) => ({ ...cur, ...patch }));
      schedule();
    },
    [schedule],
  );

  const scan = useCallback(
    (code: string, productRef?: string): Promise<ScanResult> =>
      run(async () => {
        await flushInner();
        const before = lastOrder.current?.lines ?? [];
        try {
          const o = await withRetry((version) =>
            ordersApi.scan(
              orderId,
              productRef ? { version, product_ref: productRef } : { version, barcode: code },
            ),
          );
          apply(o);
          const changed = o.lines.find((l) => {
            const b = before.find((x) => x.product_ref === l.product_ref);
            return !b || b.quantity !== l.quantity;
          });
          return { kind: 'ok', order: o, ref: changed?.product_ref ?? null } as const;
        } catch (e) {
          if (e instanceof ApiError) {
            const c = e.problemCode;
            if (e.status === 409 && c === 'ambiguous_barcode') {
              const list = e.problem?.candidates;
              return {
                kind: 'ambiguous',
                code,
                candidates: Array.isArray(list) ? (list as CatalogItem[]) : [],
              } as const;
            }
            if (e.status === 404 && (c === 'barcode_not_found' || c === 'not_orderable')) {
              return { kind: 'not_found', code, reason: c } as const;
            }
          }
          throw e;
        }
      }),
    [run, flushInner, withRetry, orderId, apply],
  );

  const importApply = useCallback(
    (lines: ImportApplyLine[]) =>
      run(async () => {
        await flushInner();
        const o = await withRetry((version) =>
          ordersApi.importApply({ order_id: orderId, version, lines }),
        );
        apply(o);
        return o;
      }),
    [run, flushInner, withRetry, orderId, apply],
  );

  const lines: OrderLine[] = useMemo(
    () =>
      order ? applyIntents(order.lines, overlay, order.limits?.allow_over_stock ?? false) : [],
    [order, overlay],
  );

  const headerValue = <K extends keyof OrderHeaderPatch>(k: K): OrderHeaderPatch[K] | undefined =>
    k in headerOverlay ? headerOverlay[k] : undefined;

  const dirty = Object.keys(overlay).length > 0 || Object.keys(headerOverlay).length > 0;
  const saveState: SaveState = inflight > 0 || dirty ? 'saving' : failed ? 'error' : 'saved';

  return {
    query,
    order,
    lines,
    saveState,
    setQty,
    setComment,
    removeLine,
    addItems,
    setHeader,
    headerValue,
    scan,
    importApply,
    flush,
    refetch,
    apply,
    versionRef,
  };
}

import { ApiError, rawRequest, request, setCsrfToken, type RawResponse } from './http';
import type {
  CatalogCategory,
  CatalogItem,
  CatalogPage,
  ImportApplyLine,
  ImportPreview,
  Order,
  OrderHeaderPatch,
  OrderState,
  PublicLink,
  ShopLoginResult,
  ShopMe,
  ShopOrderList,
} from './types';

export const shopApi = {
  link: (slug: string, signal?: AbortSignal) =>
    request<PublicLink>(`/api/shop/links/${encodeURIComponent(slug)}`, { signal }),

  async login(slug: string, phone: string): Promise<ShopLoginResult> {
    const res = await request<ShopLoginResult>('/api/shop/auth/login', {
      method: 'POST',
      body: { slug, phone },
      skipCsrf: true,
    });
    if (res.csrf_token) setCsrfToken('shop', res.csrf_token);
    return res;
  },

  async select(slug: string, clientRef: string): Promise<ShopLoginResult> {
    const res = await request<ShopLoginResult>('/api/shop/auth/select', {
      method: 'POST',
      body: { slug, client_ref: clientRef },
      skipCsrf: true,
    });
    if (res.csrf_token) setCsrfToken('shop', res.csrf_token);
    return res;
  },

  async logout(): Promise<void> {
    await request<void>('/api/shop/auth/logout', { method: 'POST' });
    setCsrfToken('shop', null);
  },

  /** Resolves to null when not signed in (401). */
  async me(signal?: AbortSignal): Promise<ShopMe | null> {
    try {
      const me = await request<ShopMe>('/api/shop/me', { signal });
      setCsrfToken('shop', me.csrf_token);
      return me;
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setCsrfToken('shop', null);
        return null;
      }
      throw err;
    }
  },
};

const O = '/api/shop/orders';

export interface ShopOrderListParams {
  state?: OrderState[];
  date_from?: string;
  date_to?: string;
  q?: string;
  page?: number;
  size?: number;
}

/** Result of a submit/cancel call: 200 (done), 202 (outcome unknown) or an error body. */
export type FlowResponse = RawResponse<Order | { detail: unknown }>;

export const ordersApi = {
  create: (body: OrderHeaderPatch = {}) => request<Order>(O, { method: 'POST', body }),
  list: (p: ShopOrderListParams) => {
    const params = new URLSearchParams();
    for (const s of p.state ?? []) params.append('state', s);
    for (const k of ['date_from', 'date_to', 'q', 'page', 'size'] as const) {
      const v = p[k];
      if (v !== undefined && v !== '') params.set(k, String(v));
    }
    const qs = params.toString();
    return request<ShopOrderList>(qs ? `${O}?${qs}` : O);
  },
  get: (id: string, signal?: AbortSignal) => request<Order>(`${O}/${id}`, { signal }),
  patchHeader: (id: string, body: OrderHeaderPatch & { version: number }) =>
    request<Order>(`${O}/${id}`, { method: 'PATCH', body }),
  remove: (id: string, version?: number) =>
    request<void>(`${O}/${id}`, { method: 'DELETE', query: { version } }),
  patchLine: (
    id: string,
    ref: string,
    body: { version: number; blocks?: number; pieces?: number; comment?: string | null },
  ) => request<Order>(`${O}/${id}/lines/${encodeURIComponent(ref)}`, { method: 'PATCH', body }),
  removeLine: (id: string, ref: string, version: number) =>
    request<Order>(`${O}/${id}/lines/${encodeURIComponent(ref)}`, {
      method: 'DELETE',
      query: { version },
    }),
  scan: (
    id: string,
    body: { version: number; barcode?: string; product_ref?: string; pieces?: number },
  ) => request<Order>(`${O}/${id}/lines/scan`, { method: 'POST', body }),
  importPreview: (id: string, file: File) => {
    const form = new FormData();
    form.set('order_id', id);
    form.set('file', file);
    return request<ImportPreview>(`${O}/import`, { method: 'POST', form });
  },
  importApply: (body: { order_id: string; version: number; lines: ImportApplyLine[] }) =>
    request<Order>(`${O}/import/apply`, { method: 'POST', body }),
  /** Resolves for 200/202/4xx/5xx so the caller can branch on the outcome. */
  submit: (id: string, version: number): Promise<FlowResponse> =>
    rawRequest(`${O}/${id}/submit`, { method: 'POST', body: { version } }),
  cancel: (id: string, version?: number): Promise<FlowResponse> =>
    rawRequest(`${O}/${id}/cancel`, { method: 'POST', body: { version } }),
  duplicate: (id: string) => request<Order>(`${O}/${id}/duplicate`, { method: 'POST' }),
};

const C = '/api/shop/catalog';
export const catalogApi = {
  search: (p: {
    q?: string;
    category?: string;
    price_type?: string;
    warehouse?: string;
    page?: number;
    size?: number;
  }) => request<CatalogPage>(C, { query: p }),
  categories: (price_type?: string) =>
    request<{ items: CatalogCategory[] }>(`${C}/categories`, { query: { price_type } }),
  barcode: (code: string, price_type?: string, warehouse?: string) =>
    request<{ items: CatalogItem[] }>(`${C}/barcode/${encodeURIComponent(code)}`, {
      query: { price_type, warehouse },
    }),
};

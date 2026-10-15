import { ApiError, rawRequest, request, setCsrfToken, type RawResponse } from './http';
import type {
  AppSettings,
  AppSettingsUpdate,
  AuditFacets,
  AuditList,
  ReportGroupBy,
  SalesReport,
  AdminClient,
  AdminLink,
  AdminLoginResult,
  AdminOrder,
  AdminOrderList,
  AdminProduct,
  AdminProductWithWarnings,
  Dashboard,
  OrderHeaderPatch,
  OrderState,
  AdminMe,
  AdminRefs,
  AdminUser,
  LinkClient,
  LinkCreateBody,
  LinkStats,
  LinkUpdateBody,
  Paged,
  PhoneFlag,
  PhoneOverrideStatus,
  PhoneStatus,
  Role,
  SdHealth,
  SyncResult,
  SyncStatus,
  TotpSetup,
} from './types';

const A = '/api/admin';
type Page = { limit: number; offset: number };

export const authApi = {
  async login(body: { username: string; password: string; totp?: string }) {
    const res = await request<AdminLoginResult>(`${A}/auth/login`, {
      method: 'POST',
      body,
      skipCsrf: true,
    });
    setCsrfToken('admin', res.csrf_token);
    return res;
  },
  async logout() {
    await request<void>(`${A}/auth/logout`, { method: 'POST' });
    setCsrfToken('admin', null);
  },
  /** Resolves to null when not signed in. */
  async me(signal?: AbortSignal): Promise<AdminMe | null> {
    try {
      const me = await request<AdminMe>(`${A}/auth/me`, { signal });
      setCsrfToken('admin', me.csrf_token);
      return me;
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setCsrfToken('admin', null);
        return null;
      }
      throw err;
    }
  },
  changePassword: (body: { current_password: string; new_password: string }) =>
    request<void>(`${A}/auth/password`, { method: 'POST', body }),
  totpSetup: (password: string) =>
    request<TotpSetup>(`${A}/auth/totp/setup`, { method: 'POST', body: { password } }),
  totpEnable: (body: { code: string; password: string }) =>
    request<void>(`${A}/auth/totp/enable`, { method: 'POST', body }),
  totpDisable: (body: { code: string; password: string }) =>
    request<void>(`${A}/auth/totp/disable`, { method: 'POST', body }),
};

export interface UserInput {
  username: string;
  full_name: string;
  role: Role;
  extra_permissions: string[];
  password?: string | null;
}

export const usersApi = {
  list: (p: Page) => request<Paged<AdminUser>>(`${A}/users`, { query: p }),
  create: (body: UserInput) =>
    request<AdminUser & { temporary_password?: string }>(`${A}/users`, { method: 'POST', body }),
  update: (
    id: number,
    body: Partial<Pick<UserInput, 'full_name' | 'role' | 'extra_permissions'>> & {
      active?: boolean;
    },
  ) => request<AdminUser>(`${A}/users/${id}`, { method: 'PATCH', body }),
  remove: (id: number) => request<void>(`${A}/users/${id}`, { method: 'DELETE' }),
  resetPassword: (id: number) =>
    request<{ temporary_password: string }>(`${A}/users/${id}/reset-password`, { method: 'POST' }),
  revokeSessions: (id: number) =>
    request<{ revoked: number }>(`${A}/users/${id}/revoke-sessions`, { method: 'POST' }),
};

export interface LinkListParams extends Page {
  q?: string;
  active?: boolean | null;
  archived?: boolean;
}

export const linksApi = {
  list: (p: LinkListParams) => request<Paged<AdminLink>>(`${A}/links`, { query: { ...p } }),
  get: (id: number) => request<AdminLink>(`${A}/links/${id}`),
  create: (body: LinkCreateBody) => request<AdminLink>(`${A}/links`, { method: 'POST', body }),
  update: (id: number, body: LinkUpdateBody) =>
    request<AdminLink>(`${A}/links/${id}`, { method: 'PATCH', body }),
  archive: (id: number) => request<AdminLink>(`${A}/links/${id}/archive`, { method: 'POST' }),
  unarchive: (id: number) => request<AdminLink>(`${A}/links/${id}/unarchive`, { method: 'POST' }),
  clone: (id: number, name?: string) =>
    request<AdminLink>(`${A}/links/${id}/clone`, { method: 'POST', body: { name: name ?? null } }),
  stats: (id: number, days = 30) =>
    request<LinkStats>(`${A}/links/${id}/stats`, { query: { days } }),
  clients: (id: number, p: Page) =>
    request<Paged<LinkClient>>(`${A}/links/${id}/clients`, { query: p }),
  changeClients: (id: number, body: { add: number[]; remove: number[] }) =>
    request<{ added: number; removed: number; total: number }>(`${A}/links/${id}/clients`, {
      method: 'PUT',
      body,
    }),
};

export interface ClientListParams extends Page {
  q?: string;
  blocked?: boolean | null;
}
export interface PhoneListParams extends Page {
  q?: string;
  status?: PhoneStatus | null;
}

export const clientsApi = {
  list: (p: ClientListParams) => request<Paged<AdminClient>>(`${A}/clients`, { query: { ...p } }),
  block: (id: number, reason: string | null) =>
    request<{ client_id: number; blocked: boolean; revoked_sessions: number }>(
      `${A}/clients/${id}/block`,
      { method: 'POST', body: { reason } },
    ),
  unblock: (id: number) =>
    request<{ client_id: number; blocked: boolean }>(`${A}/clients/${id}/unblock`, {
      method: 'POST',
    }),
  revokeSessions: (id: number) =>
    request<{ revoked: number }>(`${A}/clients/${id}/revoke-sessions`, { method: 'POST' }),
  phones: (p: PhoneListParams) => request<Paged<PhoneFlag>>(`${A}/phones`, { query: { ...p } }),
  overridePhone: (key: string, body: { status: PhoneOverrideStatus; reason?: string | null }) =>
    request<{ phone_key: string; status: PhoneStatus; reason: string | null }>(
      `${A}/phones/${key}`,
      { method: 'PUT', body },
    ),
};

export const syncApi = {
  status: () => request<SyncStatus>(`${A}/sync/status`),
  run: (entity: string) => request<SyncResult>(`${A}/sync/${entity}`, { method: 'POST' }),
  /** 503 still carries a meaningful body ({status:'error'}). */
  async health(): Promise<SdHealth> {
    const res = await rawRequest<SdHealth>(`${A}/health/sd`);
    if (res.data && typeof res.data === 'object' && 'status' in res.data) return res.data;
    throw new ApiError(res.status, `HTTP ${res.status}`, `${A}/health/sd`);
  },
};

export const refsApi = {
  /** Resolves to null when the endpoint does not exist yet (404/405) so forms fall back to free text. */
  async get(signal?: AbortSignal): Promise<AdminRefs | null> {
    const res = await rawRequest<AdminRefs>(`${A}/refs`, { signal });
    if (res.ok) return res.data;
    if (res.status === 404 || res.status === 405) return null;
    throw new ApiError(res.status, `HTTP ${res.status}`, `${A}/refs`);
  },
};

export const dashboardApi = {
  get: () => request<Dashboard>(`${A}/dashboard`),
};

export interface AdminOrderFilters {
  state?: OrderState[];
  sd_status?: number[];
  client?: number | null;
  link?: number | null;
  warehouse?: string;
  agent?: string;
  date_from?: string;
  date_to?: string;
  q?: string;
}

function orderQuery(f: AdminOrderFilters & Partial<Page>): string {
  const params = new URLSearchParams();
  for (const s of f.state ?? []) params.append('state', s);
  for (const s of f.sd_status ?? []) params.append('sd_status', String(s));
  for (const k of ['client', 'link', 'warehouse', 'agent', 'date_from', 'date_to', 'q'] as const) {
    const v = f[k];
    if (v !== undefined && v !== null && v !== '') params.set(k, String(v));
  }
  if (f.limit !== undefined) params.set('limit', String(f.limit));
  if (f.offset !== undefined) params.set('offset', String(f.offset));
  return params.toString();
}

export interface AdminOrderPatch extends OrderHeaderPatch {
  version: number;
  /** refresh the prices of existing lines (otherwise stored prices are kept) */
  reprice?: boolean;
  lines?: { product_ref: string; blocks: number; pieces: number; comment?: string | null }[];
}

export type AdminOrderResponse = RawResponse<AdminOrder | { detail: unknown }>;

export const adminOrdersApi = {
  list: (f: AdminOrderFilters & Page) => {
    const qs = orderQuery(f);
    return request<AdminOrderList>(qs ? `${A}/orders?${qs}` : `${A}/orders`);
  },
  exportUrl: (f: AdminOrderFilters) => {
    const qs = orderQuery(f);
    return qs ? `${A}/orders/export.xlsx?${qs}` : `${A}/orders/export.xlsx`;
  },
  async exportXlsx(f: AdminOrderFilters): Promise<Blob> {
    const res = await fetch(adminOrdersApi.exportUrl(f), { credentials: 'include' });
    if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}`, `${A}/orders/export.xlsx`);
    return res.blob();
  },
  get: (id: string, signal?: AbortSignal) => request<AdminOrder>(`${A}/orders/${id}`, { signal }),
  /** 200 saved/sent, 202 outcome unknown, 4xx/5xx structured problems. */
  edit: (id: string, body: AdminOrderPatch): Promise<AdminOrderResponse> =>
    rawRequest(`${A}/orders/${id}`, { method: 'PATCH', body }),
  status: (
    id: string,
    body: { status: number; shipment_date?: string | null; version?: number },
  ): Promise<AdminOrderResponse> =>
    rawRequest(`${A}/orders/${id}/status`, { method: 'POST', body }),
  resend: (id: string, version?: number): Promise<AdminOrderResponse> =>
    rawRequest(`${A}/orders/${id}/resend`, { method: 'POST', body: { version } }),
  /** Recalculates the proposed state without saving (same body as `edit`). */
  preview: (id: string, body: AdminOrderPatch): Promise<AdminOrderResponse> =>
    rawRequest(`${A}/orders/${id}/preview`, { method: 'POST', body }),
  remove: (id: string): Promise<AdminOrderResponse> =>
    rawRequest(`${A}/orders/${id}`, { method: 'DELETE' }),
};

export interface ProductListParams extends Page {
  q?: string;
  active?: boolean | null;
  hidden?: boolean | null;
  has_barcode?: boolean | null;
  category?: string;
  /** adds `price` / `stock_qty` to every item */
  price_type?: string | null;
  warehouse?: string | null;
}

export const productsApi = {
  list: (p: ProductListParams, signal?: AbortSignal) =>
    request<Paged<AdminProduct>>(`${A}/products`, { query: { ...p }, signal }),
  patch: (sdId: string, body: { hidden?: boolean; note?: string | null }) =>
    request<AdminProduct>(`${A}/products/${encodeURIComponent(sdId)}`, {
      method: 'PATCH',
      body,
    }),
  addBarcode: (sdId: string, barcode: string) =>
    request<AdminProductWithWarnings>(`${A}/products/${encodeURIComponent(sdId)}/barcodes`, {
      method: 'POST',
      body: { barcode },
    }),
  removeBarcode: (sdId: string, barcode: string) =>
    request<AdminProduct>(`${A}/products/${encodeURIComponent(sdId)}/barcodes`, {
      method: 'DELETE',
      body: { barcode },
    }),
};

// ---- audit ----
export interface AuditFilters {
  actor_type?: 'admin' | 'shop' | 'system' | null;
  actor_id?: number | null;
  action?: string | null;
  entity_type?: string | null;
  entity_id?: string | null;
  /** ISO instants (aware) */
  date_from?: string | null;
  date_to?: string | null;
}

export const auditApi = {
  list: (f: AuditFilters & Page) => request<AuditList>(`${A}/audit`, { query: { ...f } }),
  facets: () => request<AuditFacets>(`${A}/audit/facets`),
};

// ---- reports ----
export interface ReportParams {
  group_by: ReportGroupBy;
  /** YYYY-MM-DD, inclusive, dealer time zone */
  date_from: string;
  date_to: string;
  link_id?: number | null;
}

export const reportsApi = {
  sales: (p: ReportParams) => request<SalesReport>(`${A}/reports/sales`, { query: { ...p } }),
  async salesXlsx(p: ReportParams): Promise<Blob> {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(p)) if (v != null && v !== '') params.set(k, String(v));
    const url = `${A}/reports/sales.xlsx?${params.toString()}`;
    const res = await fetch(url, { credentials: 'include' });
    if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}`, url);
    return res.blob();
  },
};

// ---- runtime settings ----
export const settingsApi = {
  get: () => request<AppSettings>(`${A}/settings`),
  update: (body: AppSettingsUpdate) =>
    request<AppSettings>(`${A}/settings`, { method: 'PUT', body }),
  telegramTest: () =>
    request<{ ok: boolean; results: Record<string, string> }>(`${A}/settings/telegram/test`, {
      method: 'POST',
    }),
};

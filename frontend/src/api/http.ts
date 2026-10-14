/**
 * Minimal typed fetch layer.
 *  - same-origin (`/api` is proxied by Vite / nginx), cookies via `credentials: 'include'`
 *  - CSRF token per realm (shop/admin) kept in memory only; sent on every unsafe method
 *  - errors normalised into ApiError (status, detail, Retry-After)
 */

export type Realm = 'shop' | 'admin';

export interface FieldIssue {
  /** Dotted path of the offending field (e.g. `settings.min_order_sum`), or '' for form-level. */
  path: string;
  message: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  readonly retryAfter: number | null;
  readonly path: string;

  constructor(status: number, detail: unknown, path: string, retryAfter: number | null = null) {
    super(typeof detail === 'string' ? detail : `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.path = path;
    this.retryAfter = retryAfter;
  }

  get realm(): Realm | null {
    return realmOf(this.path);
  }

  get isNetwork(): boolean {
    return this.status === 0;
  }

  get isAuthAttempt(): boolean {
    return /\/auth\/(login|select)$/.test(this.path);
  }

  /** Plain-string detail (e.g. "totp_required", "password_change_required"). */
  get code(): string | null {
    return typeof this.detail === 'string' ? this.detail : null;
  }

  /** Machine code: plain-string detail, or `detail.code` of a structured problem. */
  get problemCode(): string | null {
    const d = this.detail;
    if (typeof d === 'string') return d;
    if (d && typeof d === 'object' && !Array.isArray(d) && 'code' in d) {
      const c = (d as { code: unknown }).code;
      return typeof c === 'string' ? c : null;
    }
    return null;
  }

  /** Structured `detail` object (e.g. `{code, errors, candidates}`), if any. */
  get problem(): Record<string, unknown> | null {
    const d = this.detail;
    return d && typeof d === 'object' && !Array.isArray(d) ? (d as Record<string, unknown>) : null;
  }

  /** Flatten FastAPI `detail` (string | string[] | pydantic error list) into field issues. */
  get issues(): FieldIssue[] {
    const d = this.detail;
    if (typeof d === 'string') return [{ path: '', message: d }];
    if (d && typeof d === 'object' && !Array.isArray(d)) {
      const m = (d as { message?: unknown }).message;
      return typeof m === 'string' ? [{ path: '', message: m }] : [];
    }
    if (!Array.isArray(d)) return [];
    return d.map((item): FieldIssue => {
      if (typeof item === 'string') return { path: '', message: item };
      if (item && typeof item === 'object') {
        const rec = item as { loc?: unknown; msg?: unknown };
        const loc = Array.isArray(rec.loc) ? rec.loc.filter((p) => p !== 'body') : [];
        return { path: loc.join('.'), message: String(rec.msg ?? 'invalid') };
      }
      return { path: '', message: String(item) };
    });
  }
}

export function realmOf(path: string): Realm | null {
  if (path.startsWith('/api/shop')) return 'shop';
  if (path.startsWith('/api/admin')) return 'admin';
  return null;
}

const csrfTokens: Record<Realm, string | null> = { shop: null, admin: null };
export const setCsrfToken = (realm: Realm, token: string | null): void => {
  csrfTokens[realm] = token;
};
export const getCsrfToken = (realm: Realm): string | null => csrfTokens[realm];

type CsrfRefresher = (realm: Realm) => Promise<void>;
let csrfRefresher: CsrfRefresher | null = null;
export const setCsrfRefresher = (fn: CsrfRefresher | null): void => {
  csrfRefresher = fn;
};

const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  /** multipart upload (the browser sets the Content-Type boundary) */
  form?: FormData;
  query?: Record<string, string | number | boolean | null | undefined>;
  signal?: AbortSignal;
  /** Don't send the CSRF header (login endpoints). */
  skipCsrf?: boolean;
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== '') params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

function parseRetryAfter(value: string | null): number | null {
  if (!value) return null;
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? Math.ceil(n) : null;
}

export interface RawResponse<T> {
  status: number;
  ok: boolean;
  data: T | null;
  retryAfter: number | null;
}

/** Like `request`, but resolves for any HTTP status (used where a non-2xx body is meaningful). */
export async function rawRequest<T>(
  path: string,
  opts: RequestOptions = {},
  retried = false,
): Promise<RawResponse<T>> {
  const method = opts.method ?? 'GET';
  const realm = realmOf(path);
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  if (UNSAFE.has(method) && realm && !opts.skipCsrf) {
    const token = csrfTokens[realm];
    if (token) headers['X-CSRF-Token'] = token;
  }

  let res: Response;
  try {
    res = await fetch(buildUrl(path, opts.query), {
      method,
      headers,
      credentials: 'include',
      body: opts.form ?? (opts.body !== undefined ? JSON.stringify(opts.body) : undefined),
      signal: opts.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err;
    throw new ApiError(0, 'network_error', path);
  }

  let data: unknown = null;
  if (res.status !== 204) {
    const text = await res.text();
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = null;
      }
    }
  }

  if (!res.ok) {
    const detail =
      data && typeof data === 'object' && 'detail' in data
        ? (data as { detail: unknown }).detail
        : `HTTP ${res.status}`;
    // A stale/missing CSRF token (e.g. server restarted): refresh via /me once and retry.
    if (
      res.status === 403 &&
      !retried &&
      realm &&
      UNSAFE.has(method) &&
      typeof detail === 'string' &&
      detail.toLowerCase().includes('csrf') &&
      csrfRefresher
    ) {
      await csrfRefresher(realm);
      return rawRequest<T>(path, opts, true);
    }
    return {
      status: res.status,
      ok: false,
      data: data as T | null,
      retryAfter: parseRetryAfter(res.headers.get('Retry-After')),
    };
  }
  return { status: res.status, ok: true, data: data as T | null, retryAfter: null };
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const res = await rawRequest<T>(path, opts);
  if (!res.ok) {
    const body = res.data as { detail?: unknown } | null;
    throw new ApiError(res.status, body?.detail ?? `HTTP ${res.status}`, path, res.retryAfter);
  }
  return res.data as T;
}

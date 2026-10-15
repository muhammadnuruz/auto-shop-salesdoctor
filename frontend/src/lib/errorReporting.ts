/**
 * Uncaught browser errors -> POST /api/client-errors (logged server-side, forwarded to Sentry
 * when configured). Same-origin, so the CSP stays `connect-src 'self'`. At most
 * MAX_REPORTS per page load and each distinct message only once; the URL is sent without
 * query string or fragment.
 */
const MAX_REPORTS = 5;
const sent = new Set<string>();
let count = 0;

export type ErrorSource = 'boundary' | 'route' | 'window' | 'promise';

function describe(error: unknown): { message: string; stack?: string } {
  if (error instanceof Error)
    return { message: `${error.name}: ${error.message}`, stack: error.stack };
  if (typeof error === 'string') return { message: error };
  try {
    return { message: JSON.stringify(error).slice(0, 500) };
  } catch {
    return { message: String(error) };
  }
}

export function reportError(error: unknown, source: ErrorSource): void {
  if (import.meta.env.MODE === 'test') return;
  const { message, stack } = describe(error);
  const key = `${source}:${message}`;
  if (count >= MAX_REPORTS || sent.has(key)) return;
  sent.add(key);
  count += 1;
  const body = JSON.stringify({
    message: message.slice(0, 500),
    stack: stack?.slice(0, 4000),
    url: `${window.location.origin}${window.location.pathname}`,
    source,
    release: import.meta.env.VITE_APP_RELEASE ?? null,
  });
  try {
    void fetch('/api/client-errors', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      keepalive: true,
      credentials: 'omit',
    }).catch(() => undefined);
  } catch {
    /* reporting must never throw */
  }
}

export function installGlobalErrorReporting(): void {
  window.addEventListener('error', (e) => reportError(e.error ?? e.message, 'window'));
  window.addEventListener('unhandledrejection', (e) => reportError(e.reason, 'promise'));
}

/** test helper */
export function _resetErrorReporting(): void {
  sent.clear();
  count = 0;
}

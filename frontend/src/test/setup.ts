import '@testing-library/jest-dom/vitest';
import { cleanup, configure } from '@testing-library/react';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { setCsrfToken } from '@/api/http';
import i18n from '@/i18n';
import { server } from './server';

// Lazy route chunks are transformed on first use: give async queries headroom on slow machines.
configure({ asyncUtilTimeout: 4000 });

// Node's fetch needs absolute URLs; the app uses same-origin relative ones (`/api/...`).
const nativeFetch = globalThis.fetch.bind(globalThis);
globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) =>
  nativeFetch(
    typeof input === 'string' && input.startsWith('/')
      ? new URL(input, window.location.origin)
      : input,
    init,
  )) as typeof fetch;

// Treat the viewport as desktop (min-width queries match); no dark-mode preference.
window.matchMedia = ((query: string) => ({
  matches: query.includes('min-width'),
  media: query,
  onchange: null,
  addEventListener: () => undefined,
  removeEventListener: () => undefined,
  addListener: () => undefined,
  removeListener: () => undefined,
  dispatchEvent: () => false,
})) as typeof window.matchMedia;

beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(async () => {
  cleanup();
  server.resetHandlers();
  setCsrfToken('shop', null);
  setCsrfToken('admin', null);
  localStorage.clear();
  await i18n.changeLanguage('ru');
});
afterAll(() => server.close());

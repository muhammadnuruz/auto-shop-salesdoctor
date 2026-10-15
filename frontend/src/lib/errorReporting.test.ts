import { afterEach, describe, expect, it, vi } from 'vitest';

describe('error reporting', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it('posts path-only reports, deduplicated and capped per page load', async () => {
    vi.stubEnv('MODE', 'production');
    const calls: { url: string; body: Record<string, unknown> }[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
      calls.push({ url: String(url), body: JSON.parse(String(init?.body)) });
      return new Response(null, { status: 204 });
    });
    window.history.replaceState(null, '', '/s/abc/home?phone=998901234567#x');
    const { reportError, _resetErrorReporting } = await import('./errorReporting');
    _resetErrorReporting();
    reportError(new TypeError('boom'), 'boundary');
    reportError(new TypeError('boom'), 'boundary'); // duplicate
    for (let i = 0; i < 10; i += 1) reportError(`e${i}`, 'window');
    expect(calls).toHaveLength(5);
    expect(calls[0]?.url).toBe('/api/client-errors');
    expect(calls[0]?.body).toMatchObject({ message: 'TypeError: boom', source: 'boundary' });
    expect(String(calls[0]?.body.url)).toMatch(/\/s\/abc\/home$/);
    expect(JSON.stringify(calls)).not.toContain('998901234567');
  });
});

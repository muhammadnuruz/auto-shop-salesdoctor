import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { AppSettings } from '@/api/types';
import { adminUser, VIEWER_PERMS } from '@/test/fixtures';
import { server } from '@/test/server';
import { renderApp } from '@/test/utils';
import { BOT_TOKEN_RE, CHAT_ID_RE } from './SettingsPage';

const me = (user: ReturnType<typeof adminUser>) =>
  http.get('*/api/admin/auth/me', () => HttpResponse.json({ user, csrf_token: 'c' }));

const interval = (d: number, min = 60, max = 86400) => ({
  default: d,
  override: null,
  effective: d,
  min,
  max,
});

const settings = (over: Partial<AppSettings> = {}): AppSettings => ({
  sync_intervals: {
    references: interval(1800, 300),
    products: interval(600),
    prices: interval(600),
    stock: interval(60, 30, 3600),
    clients: interval(1800, 300),
    balances: interval(300),
  },
  phone_max_clients: { default: 3, override: null, effective: 3 },
  telegram: {
    enabled: false,
    token_configured: false,
    token_error: false,
    chat_ids: [],
    events: ['new_order', 'order_failed', 'outcome_unknown', 'reconcile_exhausted', 'sync_error'],
    active: false,
    health: {},
  },
  sync_enabled: true,
  sd: { host: 'buxdiler.salesdoc.io', credentials_configured: true, filial_id: null },
  ...over,
});

describe('audit page', () => {
  const entry = {
    id: 9,
    at: '2026-10-14T08:00:00Z',
    actor_type: 'admin',
    actor_id: 1,
    actor_name: 'root',
    action: 'settings.update',
    entity_type: 'settings',
    entity_id: 'app',
    before: { phone_max_clients: 3 },
    after: { phone_max_clients: 5 },
    ip: '10.0.0.1',
    user_agent: 'UA',
    request_id: 'req-1',
  };

  it('lists entries, filters by action and shows details', async () => {
    const queries: URLSearchParams[] = [];
    server.use(
      me(adminUser()),
      http.get('*/api/admin/audit/facets', () =>
        HttpResponse.json({
          actions: ['order.submit', 'settings.update'],
          entity_types: ['settings', 'shop_order'],
          actors: [{ id: 1, username: 'root' }],
        }),
      ),
      http.get('*/api/admin/audit', ({ request }) => {
        queries.push(new URL(request.url).searchParams);
        return HttpResponse.json({ total: 1, items: [entry] });
      }),
    );
    renderApp('/admin/audit');
    const user = userEvent.setup();
    expect(await screen.findByText('settings.update')).toBeInTheDocument();
    expect(screen.getByText('10.0.0.1')).toBeInTheDocument();

    await user.click(screen.getByLabelText('Действие'));
    await user.click(await screen.findByRole('option', { name: 'order.submit' }));
    await waitFor(() => expect(queries.at(-1)?.get('action')).toBe('order.submit'));
    expect(queries.at(-1)?.get('offset')).toBe('0');

    await user.click(screen.getByRole('row', { name: /Подробности: settings.update/ }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/"phone_max_clients": 5/)).toBeInTheDocument();
    expect(within(dialog).getByText(/req-1/)).toBeInTheDocument();
  });

  it('is not reachable without audit.read', async () => {
    server.use(me(adminUser({ role: 'viewer', permissions: VIEWER_PERMS })));
    renderApp('/admin/audit');
    expect(await screen.findByText('Нет доступа')).toBeInTheDocument();
  });
});

describe('reports page', () => {
  it('loads the report, switches grouping and shows totals', async () => {
    const seen: URLSearchParams[] = [];
    server.use(
      me(adminUser({ role: 'viewer', permissions: VIEWER_PERMS })),
      http.get('*/api/admin/reports/sales', ({ request }) => {
        const q = new URL(request.url).searchParams;
        seen.push(q);
        return HttpResponse.json({
          group_by: q.get('group_by'),
          date_from: q.get('date_from'),
          date_to: q.get('date_to'),
          totals: { orders: 3, qty: 10, sum: 4500 },
          rows: [
            { key: 'p1', name: 'Cream', orders: 2, qty: 6, sum: 3000 },
            { key: 'p2', name: 'Soap', orders: 1, qty: 4, sum: 1500 },
          ],
          truncated: false,
        });
      }),
    );
    renderApp('/admin/reports');
    const user = userEvent.setup();
    expect(await screen.findByText('Cream')).toBeInTheDocument();
    expect(seen[0]?.get('group_by')).toBe('client');
    expect(screen.getByText('4 500')).toBeInTheDocument();
    expect(screen.getByText('66.7%')).toBeInTheDocument();

    await user.click(screen.getByLabelText('Группировка'));
    await user.click(await screen.findByRole('option', { name: 'По товарам' }));
    await waitFor(() => expect(seen.at(-1)?.get('group_by')).toBe('product'));
    await user.click(screen.getByRole('button', { name: '7 дней' }));
    await waitFor(() => {
      const q = seen.at(-1);
      expect(q?.get('date_from')).not.toBe(seen[0]?.get('date_from'));
    });
  });
});

describe('settings page', () => {
  it('saves sync intervals and phone threshold', async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      me(adminUser()),
      http.get('*/api/admin/settings', () => HttpResponse.json(settings())),
      http.put('*/api/admin/settings', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(settings());
      }),
    );
    renderApp('/admin/settings');
    const user = userEvent.setup();
    expect(await screen.findByText('buxdiler.salesdoc.io')).toBeInTheDocument();
    const stock = screen.getByLabelText('Остатки');
    await user.type(stock, '10');
    expect(await screen.findByText('Допустимо от 30 до 3600')).toBeInTheDocument();
    const syncCard = stock.closest('.MuiPaper-root') as HTMLElement;
    const saveSync = within(syncCard).getByRole('button', { name: 'Сохранить' });
    expect(saveSync).toBeDisabled();
    await user.clear(stock);
    await user.type(stock, '120');
    await user.click(saveSync);
    await waitFor(() => expect(body).toEqual({ sync_intervals: { stock: 120 } }));

    const phone = screen.getByLabelText('Макс. магазинов на один номер');
    await user.clear(phone);
    await user.type(phone, '5');
    const phoneCard = phone.closest('.MuiPaper-root') as HTMLElement;
    await user.click(within(phoneCard).getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(body).toEqual({ phone_max_clients: 5 }));
  });

  it('never shows the token, validates chat ids and sends a test', async () => {
    let body: Record<string, unknown> | null = null;
    let tested = false;
    server.use(
      me(adminUser()),
      http.get('*/api/admin/settings', () =>
        HttpResponse.json(
          settings({
            telegram: {
              ...settings().telegram,
              enabled: true,
              token_configured: true,
              chat_ids: ['-1001'],
              active: true,
              health: {
                last_ok_at: '2026-10-14T07:00:00Z',
                last_error: 'bot token rejected (http 401)',
                last_error_at: '2026-10-14T08:00:00Z',
              },
            },
          }),
        ),
      ),
      http.put('*/api/admin/settings', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(settings());
      }),
      http.post('*/api/admin/settings/telegram/test', () => {
        tested = true;
        return HttpResponse.json({ ok: true, results: { '-1001': 'ok' } });
      }),
    );
    renderApp('/admin/settings');
    const user = userEvent.setup();
    expect(await screen.findByText('Уведомления работают')).toBeInTheDocument();
    expect(screen.getByText(/bot token rejected/)).toBeInTheDocument();
    const token = screen.getByLabelText('Токен бота');
    expect(token).toHaveValue('');
    expect(screen.getByText(/Токен сохранён/)).toBeInTheDocument();

    await user.type(token, 'nope');
    expect(await screen.findByText('Формат: 123456:ABC…')).toBeInTheDocument();
    await user.clear(token);

    const chats = screen.getByLabelText('ID чатов');
    await user.type(chats, 'bad id{enter}');
    expect(await screen.findByText(/Неверный ID чата: bad id/)).toBeInTheDocument();
    const tgCard = chats.closest('.MuiPaper-root.MuiPaper-outlined') as HTMLElement;
    expect(within(tgCard).getByRole('button', { name: 'Сохранить' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Отправить тест' }));
    await waitFor(() => expect(tested).toBe(true));
    expect(await screen.findByText('Тестовое сообщение отправлено')).toBeInTheDocument();
    expect(body).toBeNull();
  });

  it('validation patterns match the backend', () => {
    expect(CHAT_ID_RE.test('-1001234567890')).toBe(true);
    expect(CHAT_ID_RE.test('@my_channel')).toBe(true);
    expect(CHAT_ID_RE.test('@abc')).toBe(false);
    expect(CHAT_ID_RE.test('12 34')).toBe(false);
    expect(BOT_TOKEN_RE.test(`123456789:${'A'.repeat(35)}`)).toBe(true);
    expect(BOT_TOKEN_RE.test('123:abc')).toBe(false);
  });
});

describe('admin navigation', () => {
  it('shows reports and settings only with the matching permissions', async () => {
    server.use(
      me(adminUser({ role: 'viewer', permissions: VIEWER_PERMS })),
      http.get('*/api/admin/dashboard', () => HttpResponse.json({}, { status: 500 })),
    );
    renderApp('/admin/reports');
    expect((await screen.findAllByText('Отчёты')).length).toBeGreaterThan(0);
    expect(screen.queryByRole('link', { name: 'Настройки' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Аудит' })).not.toBeInTheDocument();
  });
});

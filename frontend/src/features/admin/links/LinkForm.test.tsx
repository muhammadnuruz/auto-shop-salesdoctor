import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { AdminLink } from '@/api/types';
import { adminUser, linkStats } from '@/test/fixtures';
import { server } from '@/test/server';
import { renderApp } from '@/test/utils';

const baseLink = (over: Partial<AdminLink> = {}): AdminLink => ({
  id: 5,
  slug: 'k8fq2xz9',
  name: 'Created',
  description: null,
  active: true,
  state: 'active',
  starts_at: null,
  ends_at: null,
  max_logins: null,
  login_count: 0,
  client_scope: 'all',
  scope_values: [],
  settings: {
    warehouses: { mode: 'fixed', ids: ['w1'] },
    price_types: { mode: 'choice', ids: ['p1'] },
    agents: { mode: 'client_assigned', ids: [] },
    trade_directions: { mode: 'fixed', ids: ['t1'] },
    order_status: 1,
    allow_over_stock: false,
    product_filter: { mode: 'all', ids: [] },
    branding: {},
  },
  created_by: 1,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  archived_at: null,
  ...over,
});

const setup = () =>
  server.use(
    http.get('*/api/admin/auth/me', () =>
      HttpResponse.json({ user: adminUser(), csrf_token: 'admin-csrf' }),
    ),
    http.get('*/api/admin/refs', () => HttpResponse.json({ detail: 'Not Found' }, { status: 404 })),
  );

async function addChips(
  user: ReturnType<typeof userEvent.setup>,
  label: RegExp,
  ...values: string[]
) {
  const input = screen.getByRole('combobox', { name: label });
  for (const v of values) {
    await user.type(input, `${v}{Enter}`);
  }
}

describe('link form validation', () => {
  it('blocks submit and shows field errors for an empty form', async () => {
    setup();
    let posted = 0;
    server.use(
      http.post('*/api/admin/links', () => {
        posted += 1;
        return HttpResponse.json(baseLink(), { status: 201 });
      }),
    );
    renderApp('/admin/links/new');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Создать' }));

    expect(await screen.findByText('Укажите название')).toBeInTheDocument();
    // choice mode (default) with no ids for warehouses, price types, trade directions
    expect(screen.getAllByText('Укажите хотя бы один ID').length).toBe(3);
    expect(posted).toBe(0);
  });

  it('enforces fixed = exactly one id, slug pattern, min <= max, hex colour, logo url', async () => {
    setup();
    renderApp('/admin/links/new');
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(/^Название/), 'My link');
    await user.type(screen.getByLabelText('Slug'), 'ab');

    // warehouses -> fixed with 2 ids
    await user.click(screen.getByRole('combobox', { name: /Склады — режим/ }));
    await user.click(await screen.findByRole('option', { name: 'Фиксированное' }));
    await addChips(user, /Склады: ID/, 'w1', 'w2');

    await user.type(screen.getByLabelText('Мин. сумма заказа'), '500');
    await user.type(screen.getByLabelText('Макс. сумма заказа'), '100');
    await user.type(screen.getByLabelText('Цвет (HEX)'), 'red');
    await user.type(screen.getByLabelText('URL логотипа'), 'http://insecure.example/logo.png');

    await user.click(screen.getByRole('button', { name: 'Создать' }));

    expect(await screen.findByText('Только a-z, 0-9 и «-», длина 6–64')).toBeInTheDocument();
    expect(screen.getByText('Для фиксированного режима нужен ровно один ID')).toBeInTheDocument();
    expect(screen.getByText('Минимум не может быть больше максимума')).toBeInTheDocument();
    expect(screen.getByText('Формат #RRGGBB')).toBeInTheDocument();
    expect(screen.getByText('Только https://… или путь, начинающийся с /')).toBeInTheDocument();
  });

  it('requires scope values for territory scope', async () => {
    setup();
    renderApp('/admin/links/new');
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(/^Название/), 'Scoped');
    await user.click(screen.getByRole('combobox', { name: 'Кому доступна' }));
    await user.click(await screen.findByRole('option', { name: 'По территории' }));
    await user.click(screen.getByRole('button', { name: 'Создать' }));
    expect(await screen.findByText('Укажите хотя бы одно значение')).toBeInTheDocument();
  });

  it('submits a valid payload matching the API contract, then opens the edit page', async () => {
    setup();
    let body: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/admin/links', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          { ...baseLink({ slug: 'shop-test' }), warnings: ['slug_low_entropy'] },
          { status: 201 },
        );
      }),
      http.get('*/api/admin/links/5', () => HttpResponse.json(baseLink({ slug: 'shop-test' }))),
      http.get('*/api/admin/links/5/stats', () => HttpResponse.json(linkStats())),
    );
    const { router } = renderApp('/admin/links/new');
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(/^Название/), '  Bozor  ');
    await user.type(screen.getByLabelText('Slug'), 'shop-test');
    await addChips(user, /Склады: ID/, 'w1');
    await addChips(user, /Типы цен: ID/, 'p1', 'p2');
    await addChips(user, /Направления торговли: ID/, 't1');
    await user.type(screen.getByLabelText('Мин. сумма заказа'), '1000');
    await user.type(screen.getByLabelText('Макс. число входов'), '25');
    await user.click(screen.getByRole('button', { name: 'Создать' }));

    await waitFor(() => expect(router.state.location.pathname).toBe('/admin/links/5'));
    expect(body).toMatchObject({
      slug: 'shop-test',
      name: 'Bozor',
      active: true,
      max_logins: 25,
      client_scope: 'all',
      scope_values: [],
      starts_at: null,
      ends_at: null,
      settings: {
        warehouses: { mode: 'choice', ids: ['w1'] },
        price_types: { mode: 'choice', ids: ['p1', 'p2'] },
        agents: { mode: 'client_assigned', ids: [] },
        trade_directions: { mode: 'choice', ids: ['t1'] },
        order_status: 1,
        min_order_sum: 1000,
        max_order_sum: null,
        allow_over_stock: false,
        product_filter: { mode: 'all', ids: [] },
      },
    });
    // edit page: full URL, copy button, QR download, stats and the low-entropy warning
    expect(await screen.findByLabelText('Полная ссылка')).toHaveValue(
      `${window.location.origin}/s/shop-test`,
    );
    expect(screen.getByRole('button', { name: 'Копировать ссылку' })).toBeInTheDocument();
    expect(await screen.findByRole('link', { name: 'Скачать PNG' })).toHaveAttribute(
      'download',
      'shop-test.png',
    );
    expect(screen.getByText(/Slug легко угадать/)).toBeInTheDocument();
    expect(await screen.findByText('3 / 10')).toBeInTheDocument();
    // extended statistics panel
    expect(await screen.findByText('Статистика ссылки')).toBeInTheDocument();
    expect(screen.getByText('Shop Seven')).toBeInTheDocument();
    expect(screen.getAllByText('5 000').length).toBeGreaterThan(0);
  });

  it('maps server 422 / 409 errors onto fields', async () => {
    setup();
    server.use(
      http.post('*/api/admin/links', () =>
        HttpResponse.json(
          { detail: ["warehouses: 'zzz' is unknown or inactive"] },
          { status: 422 },
        ),
      ),
    );
    renderApp('/admin/links/new');
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(/^Название/), 'Bozor');
    await addChips(user, /Склады: ID/, 'zzz');
    await addChips(user, /Типы цен: ID/, 'p1');
    await addChips(user, /Направления торговли: ID/, 't1');
    await user.click(screen.getByRole('button', { name: 'Создать' }));
    expect(await screen.findByText("warehouses: 'zzz' is unknown or inactive")).toBeInTheDocument();

    server.use(
      http.post('*/api/admin/links', () =>
        HttpResponse.json({ detail: 'Slug already in use' }, { status: 409 }),
      ),
    );
    await user.type(screen.getByLabelText('Slug'), 'taken-slug');
    await user.click(screen.getByRole('button', { name: 'Создать' }));
    const slugField = screen.getByLabelText('Slug').closest('.MuiFormControl-root') as HTMLElement;
    expect(await within(slugField).findByText('Slug already in use')).toBeInTheDocument();
  });

  it('offers reference data as options when /api/admin/refs exists', async () => {
    setup();
    server.use(
      http.get('*/api/admin/refs', () =>
        HttpResponse.json({
          warehouses: [
            { id: 'w1', name: 'Main warehouse' },
            { id: 'w2', name: 'Other' },
          ],
          territories: [{ id: '12', clients: 5 }],
        }),
      ),
    );
    renderApp('/admin/links/new');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('combobox', { name: /Склады: ID/ }));
    expect(await screen.findByRole('option', { name: 'Main warehouse (w1)' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Other (w2)' })).toBeInTheDocument();
  });

  it('shows territories and client categories as "id (clients: N)" from the real refs shape', async () => {
    setup();
    server.use(
      http.get('*/api/admin/refs', () =>
        HttpResponse.json({
          territories: [{ id: '12', clients: 5 }],
          client_categories: [{ id: '3', clients: 9 }],
        }),
      ),
    );
    renderApp('/admin/links/new');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('combobox', { name: 'Кому доступна' }));
    await user.click(await screen.findByRole('option', { name: 'По территории' }));
    await user.click(await screen.findByRole('combobox', { name: /Значения/ }));
    expect(await screen.findByRole('option', { name: '12 (клиентов: 5)' })).toBeInTheDocument();
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('combobox', { name: 'Кому доступна' }));
    await user.click(await screen.findByRole('option', { name: 'По категории клиента' }));
    await user.click(await screen.findByRole('combobox', { name: /Значения/ }));
    expect(await screen.findByRole('option', { name: '3 (клиентов: 9)' })).toBeInTheDocument();
  });
});

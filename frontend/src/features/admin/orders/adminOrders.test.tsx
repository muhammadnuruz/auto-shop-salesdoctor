import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { AdminOrder } from '@/api/types';
import {
  ORDER_ID,
  adminOrderFixture,
  adminRefs,
  adminUser,
  invoiceFixture,
  orderLine,
} from '@/test/fixtures';
import { server } from '@/test/server';
import { renderApp } from '@/test/utils';

const OPERATOR_NO_CANCEL = ['orders.read', 'orders.write', 'products.read', 'links.read'];
const VIEWER = ['orders.read', 'links.read', 'reports.read'];

function setup(permissions?: string[]) {
  server.use(
    http.get('*/api/admin/auth/me', () =>
      HttpResponse.json({
        user: adminUser(permissions ? { role: 'viewer', permissions } : {}),
        csrf_token: 'admin-csrf',
      }),
    ),
    http.get('*/api/admin/refs', () => HttpResponse.json(adminRefs)),
    http.get('*/api/admin/clients', () => HttpResponse.json({ total: 0, items: [] })),
    http.get('*/api/admin/links', () => HttpResponse.json({ total: 0, items: [] })),
  );
}

const listBody = (over = {}) => ({
  total: 1,
  summary: { count: 1, total_sum: 2000, total_qty: 2 },
  items: [
    {
      ...adminOrderFixture(),
      lines: undefined,
      events: undefined,
    },
  ],
  ...over,
});

describe('admin orders list', () => {
  it('applies filters from the URL, sends them to the API and shows summary totals', async () => {
    setup();
    const seen: URL[] = [];
    server.use(
      http.get('*/api/admin/orders', ({ request }) => {
        seen.push(new URL(request.url));
        return HttpResponse.json(listBody());
      }),
    );
    renderApp('/admin/orders?state=failed&state=outcome_unknown&client=7&date_from=2026-10-01');
    const row = await screen.findByRole('link', { name: 'WEB-000001' });
    expect(row).toHaveAttribute('href', `/admin/orders/${ORDER_ID}`);
    const first = seen[0] as URL;
    expect(first.searchParams.getAll('state')).toEqual(['failed', 'outcome_unknown']);
    expect(first.searchParams.get('client')).toBe('7');
    expect(first.searchParams.get('date_from')).toBe('2026-10-01');
    expect(first.searchParams.get('limit')).toBe('25');
    const summary = screen.getByTestId('orders-summary');
    expect(summary).toHaveTextContent('Заказов: 1');
    expect(summary).toHaveTextContent('2 000 сум');
  });

  it('changing the SD status, warehouse and search filters re-queries the server', async () => {
    setup();
    const seen: URL[] = [];
    server.use(
      http.get('*/api/admin/orders', ({ request }) => {
        seen.push(new URL(request.url));
        return HttpResponse.json(listBody());
      }),
    );
    renderApp('/admin/orders');
    await screen.findByRole('link', { name: 'WEB-000001' });
    const user = userEvent.setup();

    await user.click(screen.getByRole('combobox', { name: 'Статус в SD' }));
    await user.click(await screen.findByRole('option', { name: 'Новый' }));
    await user.keyboard('{Escape}');
    await waitFor(() => expect(seen.at(-1)?.searchParams.getAll('sd_status')).toEqual(['1']));

    await user.click(screen.getByRole('combobox', { name: 'Склад' }));
    await user.click(await screen.findByRole('option', { name: 'Second warehouse' }));
    await waitFor(() => expect(seen.at(-1)?.searchParams.get('warehouse')).toBe('w2'));
    // earlier filters are kept
    expect(seen.at(-1)?.searchParams.getAll('sd_status')).toEqual(['1']);

    await user.type(screen.getByLabelText('Поиск'), 'WEB-0');
    await waitFor(() => expect(seen.at(-1)?.searchParams.get('q')).toBe('WEB-0'), {
      timeout: 2000,
    });
  });

  it('exports with the current filters', async () => {
    setup();
    const exported: URL[] = [];
    server.use(
      http.get('*/api/admin/orders', () => HttpResponse.json(listBody())),
      http.get('*/api/admin/orders/export.xlsx', ({ request }) => {
        exported.push(new URL(request.url));
        return new HttpResponse(new Uint8Array([80, 75]), {
          headers: {
            'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          },
        });
      }),
    );
    URL.createObjectURL = () => 'blob:x';
    URL.revokeObjectURL = () => undefined;
    renderApp('/admin/orders?state=sent');
    await screen.findByRole('link', { name: 'WEB-000001' });
    await userEvent.setup().click(screen.getByRole('button', { name: 'Экспорт в Excel' }));
    await waitFor(() => expect(exported).toHaveLength(1));
    expect(exported[0]?.searchParams.getAll('state')).toEqual(['sent']);
  });
});

describe('admin order detail', () => {
  const serve = (order: AdminOrder) =>
    server.use(http.get(`*/api/admin/orders/${ORDER_ID}`, () => HttpResponse.json(order)));

  it('sent orders open the waybill (PDF in the UI language)', async () => {
    setup(VIEWER);
    serve(adminOrderFixture());
    server.use(
      http.get(`*/api/admin/orders/${ORDER_ID}/invoice`, () => HttpResponse.json(invoiceFixture())),
    );
    renderApp(`/admin/orders/${ORDER_ID}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Накладная' }));
    const dialog = await screen.findByRole('dialog', { name: 'Печать накладной' });
    expect(await within(dialog).findByTestId('invoice')).toHaveTextContent('НАКЛАДНАЯ № 53146');
    expect(within(dialog).getByRole('link', { name: 'Скачать PDF' })).toHaveAttribute(
      'href',
      `/api/admin/orders/${ORDER_ID}/invoice.pdf?lang=ru`,
    );
  });

  it('drafts and failed orders have no waybill', async () => {
    setup(VIEWER);
    serve(adminOrderFixture({ state: 'failed', sd_id: null }));
    renderApp(`/admin/orders/${ORDER_ID}`);
    await screen.findByText('У вас нет права изменять заказы.');
    expect(screen.queryByRole('button', { name: 'Накладная' })).not.toBeInTheDocument();
  });

  it('read-only viewers get no edit / status / delete actions and see the timeline', async () => {
    setup(VIEWER);
    serve(adminOrderFixture());
    renderApp(`/admin/orders/${ORDER_ID}`);
    expect(await screen.findByText('У вас нет права изменять заказы.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Изменить статус/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Сохранить/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Удалить заказ' })).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'В штуках: Oil filter' })).toBeDisabled();
    const timeline = screen.getByRole('list', { name: 'История' });
    expect(within(timeline).getByText('Создан')).toBeInTheDocument();
    expect(within(timeline).getByText('Принят Sales Doctor')).toBeInTheDocument();
  });

  it('status change: cancel (5) needs orders.cancel, other statuses are sent with the version', async () => {
    setup(OPERATOR_NO_CANCEL);
    serve(adminOrderFixture());
    const posted: unknown[] = [];
    server.use(
      http.post(`*/api/admin/orders/${ORDER_ID}/status`, async ({ request }) => {
        posted.push(await request.json());
        return HttpResponse.json(adminOrderFixture({ sd_status: 2, version: 4 }));
      }),
    );
    renderApp(`/admin/orders/${ORDER_ID}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Изменить статус' }));
    const dialog = await screen.findByRole('dialog', { name: 'Изменить статус' });
    await user.click(within(dialog).getByRole('combobox', { name: 'Новый статус' }));
    const cancel = await screen.findByRole('option', { name: '5. Отменён' });
    expect(cancel).toHaveAttribute('aria-disabled', 'true');
    await user.click(screen.getByRole('option', { name: '2. Отгружен' }));
    await user.click(within(dialog).getByRole('button', { name: 'Подтвердить' }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toEqual({ status: 2, shipment_date: null, version: 3 });
    // no delete without orders.cancel
    expect(screen.queryByRole('button', { name: 'Удалить заказ' })).not.toBeInTheDocument();
  });

  it('superadmin can cancel via status 5 and delete with confirmation', async () => {
    setup();
    serve(adminOrderFixture());
    const posted: unknown[] = [];
    let deleted = 0;
    server.use(
      http.post(`*/api/admin/orders/${ORDER_ID}/status`, async ({ request }) => {
        posted.push(await request.json());
        return HttpResponse.json({
          ...adminOrderFixture({ sd_status: 5, state: 'cancelled', version: 4 }),
          warnings: [],
        });
      }),
      http.delete(`*/api/admin/orders/${ORDER_ID}`, () => {
        deleted += 1;
        return HttpResponse.json(adminOrderFixture({ state: 'deleted' }));
      }),
    );
    renderApp(`/admin/orders/${ORDER_ID}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Изменить статус' }));
    const dialog = await screen.findByRole('dialog', { name: 'Изменить статус' });
    await user.click(within(dialog).getByRole('combobox', { name: 'Новый статус' }));
    await user.click(await screen.findByRole('option', { name: '5. Отменён' }));
    await user.click(within(dialog).getByRole('button', { name: 'Подтвердить' }));
    await waitFor(() => expect(posted).toEqual([{ status: 5, shipment_date: null, version: 3 }]));

    await user.click(await screen.findByRole('button', { name: 'Удалить заказ' }));
    const confirm = await screen.findByRole('dialog', { name: 'Удалить заказ' });
    expect(deleted).toBe(0);
    await user.click(within(confirm).getByRole('button', { name: 'Удалить заказ' }));
    await waitFor(() => expect(deleted).toBe(1));
  });

  it('edits lines, saves with the version (re-sends to SD) and maps 422 errors inline', async () => {
    setup();
    serve(adminOrderFixture());
    const patches: Record<string, unknown>[] = [];
    let attempt = 0;
    server.use(
      http.patch(`*/api/admin/orders/${ORDER_ID}`, async ({ request }) => {
        patches.push((await request.json()) as Record<string, unknown>);
        attempt += 1;
        if (attempt === 1) {
          return HttpResponse.json(
            {
              detail: {
                code: 'validation_failed',
                errors: [{ product_ref: 'p1', code: 'over_stock', available: 3, requested: 5 }],
              },
            },
            { status: 422 },
          );
        }
        return HttpResponse.json(
          adminOrderFixture({ version: 4, lines: [orderLine({ pieces: 5, quantity: 5 })] }),
        );
      }),
    );
    renderApp(`/admin/orders/${ORDER_ID}`);
    const user = userEvent.setup();
    const save = await screen.findByRole('button', { name: 'Сохранить и отправить в SD' });
    expect(save).toBeDisabled();
    const plus = screen.getByRole('button', { name: 'В штуках: Oil filter +' });
    await user.click(plus);
    await user.click(plus);
    await user.click(plus);
    expect(screen.getByTestId('local-total')).toHaveTextContent('5 000 сум');
    await user.click(save);
    expect(
      await screen.findByText('Недостаточно на складе: доступно 3, заказано 5'),
    ).toBeInTheDocument();
    expect(patches[0]).toEqual({
      version: 3,
      lines: [{ product_ref: 'p1', blocks: 0, pieces: 5, comment: null }],
    });
    // header untouched -> not sent
    expect(patches[0]).not.toHaveProperty('warehouse');
    await user.click(screen.getByRole('button', { name: 'Сохранить и отправить в SD' }));
    await waitFor(() => expect(patches).toHaveLength(2));
    expect(await screen.findByText('Сохранено')).toBeInTheDocument();
  });

  it('lets an operator resend a failed order', async () => {
    setup(OPERATOR_NO_CANCEL);
    serve(adminOrderFixture({ state: 'failed', sd_status: null, sd_id: null, editable: true }));
    let resent = 0;
    server.use(
      http.post(`*/api/admin/orders/${ORDER_ID}/resend`, async () => {
        resent += 1;
        return HttpResponse.json(adminOrderFixture({ version: 5 }));
      }),
    );
    renderApp(`/admin/orders/${ORDER_ID}`);
    await userEvent
      .setup()
      .click(await screen.findByRole('button', { name: 'Отправить повторно' }));
    await waitFor(() => expect(resent).toBe(1));
  });

  it('recalculates unsaved edits through /preview (debounced) and shows the server totals', async () => {
    setup();
    serve(adminOrderFixture());
    const previews: Record<string, unknown>[] = [];
    server.use(
      http.post(`*/api/admin/orders/${ORDER_ID}/preview`, async ({ request }) => {
        previews.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json(
          adminOrderFixture({
            lines: [orderLine({ pieces: 4, quantity: 4, price: 1100, line_sum: 4400 })],
            totals: { qty: 4, sum: 4400, volume: 1.5, lines_count: 1 },
            price_drift: [{ product_ref: 'p1', kept_price: 1000, current_price: 1100 }],
          }),
        );
      }),
    );
    renderApp(`/admin/orders/${ORDER_ID}`);
    const user = userEvent.setup();
    const plus = await screen.findByRole('button', { name: 'В штуках: Oil filter +' });
    await user.click(plus);
    await user.click(plus);
    await waitFor(() => expect(screen.getByTestId('local-total')).toHaveTextContent('4 400 сум'));
    expect(previews).toHaveLength(1); // two clicks, one debounced request
    expect(previews[0]).toEqual({
      version: 3,
      lines: [{ product_ref: 'p1', blocks: 0, pieces: 4, comment: null }],
    });
    expect(await screen.findByTestId('price-drift')).toBeInTheDocument();
  });

  it('delete / status answering 202 with the order body keep the user on the page', async () => {
    setup();
    serve(adminOrderFixture());
    server.use(
      http.delete(`*/api/admin/orders/${ORDER_ID}`, () => {
        const pending = adminOrderFixture({
          state: 'outcome_unknown',
          last_error: { code: 'outcome_unknown', op: { name: 'delete' } },
        });
        serve(pending); // the refetch after the action sees the same state
        return HttpResponse.json(pending, { status: 202 });
      }),
    );
    renderApp(`/admin/orders/${ORDER_ID}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Удалить заказ' }));
    const dlg = await screen.findByRole('dialog', { name: 'Удалить заказ' });
    await user.click(within(dlg).getByRole('button', { name: 'Удалить заказ' }));
    expect(await screen.findByTestId('awaiting')).toHaveTextContent('удаление');
  });

  it('shows price drift and reprices on demand; shows the pending operation of unknown orders', async () => {
    setup();
    serve(
      adminOrderFixture({
        price_drift: [{ product_ref: 'p1', kept_price: 1000, current_price: 1200 }],
      }),
    );
    const bodies: unknown[] = [];
    server.use(
      http.patch(`*/api/admin/orders/${ORDER_ID}`, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(
          adminOrderFixture({
            version: 4,
            repriced: [{ product_ref: 'p1', old: 1000, new: 1200 }],
          }),
        );
      }),
    );
    renderApp(`/admin/orders/${ORDER_ID}`);
    const drift = await screen.findByTestId('price-drift');
    expect(within(drift).getByText('Oil filter: в заказе 1 000, сейчас 1 200')).toBeInTheDocument();
    await userEvent.setup().click(within(drift).getByRole('button', { name: 'Пересчитать цены' }));
    await waitFor(() => expect(bodies).toEqual([{ version: 3, reprice: true }]));
    expect(await screen.findByText('Цены пересчитаны: 1')).toBeInTheDocument();
  });

  it('explains which operation an outcome_unknown order is waiting for', async () => {
    setup();
    serve(
      adminOrderFixture({
        state: 'outcome_unknown',
        last_error: { code: 'outcome_unknown', op: { name: 'cancel' }, exhausted: true },
      }),
    );
    renderApp(`/admin/orders/${ORDER_ID}`);
    const note = await screen.findByTestId('awaiting');
    expect(note).toHaveTextContent('Ожидает подтверждения: отмена');
    expect(note).toHaveTextContent('Отправить повторно');
  });

  it('a shipped order is not editable even for admins', async () => {
    setup();
    serve(adminOrderFixture({ sd_status: 2 }));
    renderApp(`/admin/orders/${ORDER_ID}`);
    expect(await screen.findByRole('textbox', { name: 'В штуках: Oil filter' })).toBeDisabled();
    expect(
      screen.queryByRole('button', { name: 'Сохранить и отправить в SD' }),
    ).not.toBeInTheDocument();
  });
});

describe('admin dashboard', () => {
  const dashboard = {
    periods: {
      today: { orders: 3, sum: 300000 },
      '7d': { orders: 10, sum: 1000000 },
      '30d': { orders: 40, sum: 5000000 },
    },
    by_state: { sent: { orders: 30, sum: 4000000 }, failed: { orders: 2, sum: 10000 } },
    top_clients: [{ client_id: 7, name: 'Bozor Market', orders: 5, sum: 900000 }],
    top_products: [{ product_ref: 'p1', name: 'Oil filter', qty: 120, sum: 120000 }],
    by_link: [{ link_id: 3, slug: 'demo', name: 'Demo link', orders: 7, sum: 700000 }],
    failed: 2,
    outcome_unknown: 1,
    reconcile_exhausted: 1,
    last_sync: {
      products: {
        status: 'success',
        started_at: '2026-10-14T07:00:00Z',
        finished_at: '2026-10-14T07:01:00Z',
        items: 500,
        error: null,
      },
    },
  };

  it('shows KPIs, breakdowns and problem counters that link to filtered orders', async () => {
    setup();
    server.use(http.get('*/api/admin/dashboard', () => HttpResponse.json(dashboard)));
    renderApp('/admin');
    expect(await screen.findByTestId('kpi-today')).toHaveTextContent('300 000 сум');
    expect(screen.getByTestId('kpi-7d')).toHaveTextContent('Заказов: 10');
    expect(screen.getByTestId('kpi-30d')).toHaveTextContent('5 000 000 сум');
    expect(screen.getByTestId('count-failed')).toHaveTextContent('2');
    expect(screen.getByTestId('count-outcome_unknown')).toHaveTextContent('1');
    expect(screen.getByTestId('count-exhausted')).toHaveTextContent('Сверка остановлена: 1');
    expect(screen.getByTestId('count-failed').closest('a')).toHaveAttribute(
      'href',
      '/admin/orders?state=failed',
    );
    expect(screen.getByTestId('count-outcome_unknown').closest('a')).toHaveAttribute(
      'href',
      '/admin/orders?state=outcome_unknown',
    );
    expect(screen.getByRole('link', { name: 'Bozor Market' })).toHaveAttribute(
      'href',
      '/admin/orders?client=7',
    );
    expect(screen.getByText('Oil filter')).toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'Последняя синхронизация' })).toBeInTheDocument();
  });

  it('operators without reports.read see shortcuts instead of an error', async () => {
    setup(OPERATOR_NO_CANCEL);
    renderApp('/admin');
    expect(await screen.findByText(/У вас нет доступа к отчётам/)).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: 'Заказы' }).length).toBeGreaterThan(0);
  });
});

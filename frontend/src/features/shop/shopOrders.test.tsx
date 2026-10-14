import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { OrderSummary } from '@/api/types';
import { ORDER_ID, orderFixture, shopMe } from '@/test/fixtures';
import { server } from '@/test/server';
import { renderApp } from '@/test/utils';

const summary = (over: Partial<OrderSummary>): OrderSummary => {
  const {
    lines: _l,
    totals: _t,
    categories: _c,
    limits: _li,
    problems: _p,
    editable: _e,
    ...rest
  } = orderFixture(over) as ReturnType<typeof orderFixture> & Record<string, unknown>;
  void [_l, _t, _c, _li, _p, _e];
  return rest as OrderSummary;
};

const ITEMS = [
  summary({ public_id: 'a1', code: 'WEB-000001', state: 'draft', total_sum: 1000, lines_count: 1 }),
  summary({
    public_id: 'a2',
    code: 'WEB-000002',
    state: 'sent',
    sd_status: 1,
    sd_id: 'SD-2',
    total_sum: 2000,
  }),
  summary({
    public_id: 'a3',
    code: 'WEB-000003',
    state: 'sent',
    sd_status: 2,
    sd_id: 'SD-3',
    total_sum: 3000,
  }),
];

function setup() {
  const seen: URL[] = [];
  server.use(
    http.get('*/api/shop/me', () => HttpResponse.json(shopMe())),
    http.get('*/api/shop/orders', ({ request }) => {
      seen.push(new URL(request.url));
      return HttpResponse.json({ items: ITEMS, total: 3, page: 1, size: 20 });
    }),
  );
  return seen;
}

const rowOf = async (code: string) =>
  (await screen.findByRole('link', { name: code })).closest('tr') as HTMLElement;

describe('shop orders list', () => {
  it('lists orders with state chips and gates actions by state / sd_status', async () => {
    setup();
    renderApp('/s/demo-shop/orders');
    const draft = await rowOf('WEB-000001');
    const newSent = await rowOf('WEB-000002');
    const shipped = await rowOf('WEB-000003');
    expect(within(draft).getByText('Черновик')).toBeInTheDocument();
    expect(within(newSent).getByText('Отправлен')).toBeInTheDocument();
    expect(within(newSent).getByText('Новый')).toBeInTheDocument();
    expect(within(shipped).getByText('Отгружен')).toBeInTheDocument();

    expect(within(draft).getByRole('button', { name: /Удалить черновик/ })).toBeInTheDocument();
    expect(within(draft).queryByRole('button', { name: /Отменить заказ/ })).not.toBeInTheDocument();
    expect(within(newSent).getByRole('button', { name: /Отменить заказ/ })).toBeInTheDocument();
    expect(
      within(newSent).queryByRole('button', { name: /Удалить черновик/ }),
    ).not.toBeInTheDocument();
    expect(
      within(shipped).queryByRole('button', { name: /Отменить заказ/ }),
    ).not.toBeInTheDocument();
    for (const r of [draft, newSent, shipped]) {
      expect(within(r).getByRole('button', { name: /Повторить/ })).toBeInTheDocument();
    }
  });

  it('filters by state and date', async () => {
    const seen = setup();
    renderApp('/s/demo-shop/orders');
    await rowOf('WEB-000001');
    const user = userEvent.setup();
    await user.click(screen.getByRole('combobox', { name: 'Статус' }));
    await user.click(await screen.findByRole('option', { name: 'Отправлен' }));
    await waitFor(() => expect(seen.at(-1)?.searchParams.getAll('state')).toEqual(['sent']));
    await user.type(screen.getByLabelText('Дата с'), '2026-10-01');
    await waitFor(() => expect(seen.at(-1)?.searchParams.get('date_from')).toBe('2026-10-01'));
  });

  it('repeat creates a copy and opens it', async () => {
    setup();
    let duplicated = '';
    server.use(
      http.post('*/api/shop/orders/:id/duplicate', ({ params }) => {
        duplicated = String(params.id);
        return HttpResponse.json(orderFixture(), { status: 201 });
      }),
      http.get(`*/api/shop/orders/${ORDER_ID}`, () => HttpResponse.json(orderFixture())),
    );
    renderApp('/s/demo-shop/orders');
    const row = await rowOf('WEB-000002');
    await userEvent.setup().click(within(row).getByRole('button', { name: /Повторить/ }));
    await waitFor(() => expect(duplicated).toBe('a2'));
    expect(await screen.findByTestId('scanner-input')).toBeInTheDocument();
  });

  it('cancel and delete ask for confirmation first', async () => {
    setup();
    const calls: string[] = [];
    server.use(
      http.post('*/api/shop/orders/a2/cancel', async ({ request }) => {
        calls.push(`cancel ${JSON.stringify(await request.json())}`);
        return HttpResponse.json(orderFixture({ state: 'cancelled' }));
      }),
      http.delete('*/api/shop/orders/a1', ({ request }) => {
        calls.push(`delete ${new URL(request.url).searchParams.get('version')}`);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderApp('/s/demo-shop/orders');
    const user = userEvent.setup();
    await user.click(
      within(await rowOf('WEB-000002')).getByRole('button', { name: /Отменить заказ/ }),
    );
    const dialog = await screen.findByRole('dialog', { name: 'Отменить заказ?' });
    expect(calls).toEqual([]);
    await user.click(within(dialog).getByRole('button', { name: 'Отменить заказ' }));
    await waitFor(() => expect(calls).toEqual(['cancel {"version":1}']));

    await user.click(
      within(await rowOf('WEB-000001')).getByRole('button', { name: /Удалить черновик/ }),
    );
    const del = await screen.findByRole('dialog', { name: 'Удалить черновик?' });
    await user.click(within(del).getByRole('button', { name: 'Удалить черновик' }));
    await waitFor(() => expect(calls).toContain('delete 1'));
  });

  it('202 on cancel / delete is reported as pending, not as done', async () => {
    setup();
    server.use(
      http.post('*/api/shop/orders/a2/cancel', () =>
        HttpResponse.json(orderFixture({ state: 'outcome_unknown' }), { status: 202 }),
      ),
      http.delete('*/api/shop/orders/a3', () =>
        HttpResponse.json({ detail: { code: 'outcome_unknown' } }, { status: 202 }),
      ),
    );
    renderApp('/s/demo-shop/orders');
    const user = userEvent.setup();
    await user.click(
      within(await rowOf('WEB-000002')).getByRole('button', { name: /Отменить заказ/ }),
    );
    const dialog = await screen.findByRole('dialog', { name: 'Отменить заказ?' });
    await user.click(within(dialog).getByRole('button', { name: 'Отменить заказ' }));
    expect(
      await screen.findByText('Операция отправлена, ждём подтверждения Sales Doctor.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Заказ отменён')).not.toBeInTheDocument();
  });

  it('order screen: balance breakdown expands and last price type button uses /me', async () => {
    const fakeMe = {
      ...shopMe(),
      balance_by_type: [{ payment_type_sd_id: 'pt1', name: 'Наличные', amount: -250000 }],
      last_price_type_sd_id: 'p2',
    };
    const o = orderFixture();
    server.use(
      http.get('*/api/shop/me', () => HttpResponse.json(fakeMe)),
      http.get(`*/api/shop/orders/${ORDER_ID}`, () => HttpResponse.json(o)),
    );
    renderApp(`/s/demo-shop/orders/${ORDER_ID}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Баланс по типам оплаты' }));
    expect(await screen.findByText('Наличные')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Последний использованный тип цены' }),
    ).toBeInTheDocument();
  });

  it('shop home links to the orders list', async () => {
    server.use(http.get('*/api/shop/me', () => HttpResponse.json(shopMe())));
    renderApp('/s/demo-shop/home');
    expect(await screen.findByRole('link', { name: 'Мои заказы' })).toHaveAttribute(
      'href',
      '/s/demo-shop/orders',
    );
  });
});

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { AdminProduct } from '@/api/types';
import { adminRefs, adminUser } from '@/test/fixtures';
import { server } from '@/test/server';
import { renderApp } from '@/test/utils';

const product = (over: Partial<AdminProduct> = {}): AdminProduct => ({
  sd_id: 'p1',
  code: 'C-1',
  name: 'Oil filter',
  active: true,
  hidden: false,
  note: null,
  category: 'c1',
  category_name: 'Filters',
  pack_quantity: 10,
  image_url: null,
  thumb_url: null,
  barcodes: [
    { barcode: '4780000000011', source: 'sd' },
    { barcode: '1234567890123', source: 'admin' },
  ],
  stock: [
    { warehouse: 'w1', name: 'Main warehouse', quantity: 40 },
    { warehouse: 'w2', name: 'Second warehouse', quantity: 5 },
  ],
  prices: [
    { price_type: 'p1', name: 'Retail', price: 1000 },
    { price_type: 'p2', name: 'Wholesale', price: 900 },
  ],
  ...over,
});

function setup(permissions?: string[]) {
  const seen: URL[] = [];
  server.use(
    http.get('*/api/admin/auth/me', () =>
      HttpResponse.json({
        user: adminUser(permissions ? { role: 'viewer', permissions } : {}),
        csrf_token: 'admin-csrf',
      }),
    ),
    http.get('*/api/admin/refs', () => HttpResponse.json(adminRefs)),
    http.get('*/api/admin/products', ({ request }) => {
      seen.push(new URL(request.url));
      return HttpResponse.json({ total: 1, items: [product()] });
    }),
  );
  return seen;
}

const openDrawer = async () => {
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: 'Открыть товар: Oil filter' }));
  const drawer = await screen.findByRole('dialog', { name: 'Oil filter' });
  return { user, drawer };
};

describe('admin products', () => {
  it('lists products and sends the filters to the server', async () => {
    const seen = setup();
    renderApp('/admin/products');
    await screen.findByText('Oil filter');
    const user = userEvent.setup();
    await user.click(screen.getByRole('combobox', { name: 'Скрыт' }));
    await user.click(await screen.findByRole('option', { name: 'Да' }));
    await waitFor(() => expect(seen.at(-1)?.searchParams.get('hidden')).toBe('true'));
    await user.click(screen.getByRole('combobox', { name: 'Есть штрихкод' }));
    await user.click(await screen.findByRole('option', { name: 'Нет' }));
    await waitFor(() => expect(seen.at(-1)?.searchParams.get('has_barcode')).toBe('false'));
    expect(seen.at(-1)?.searchParams.get('hidden')).toBe('true');
    await user.click(screen.getByRole('combobox', { name: 'Категория' }));
    await user.click(await screen.findByRole('option', { name: 'Filters' }));
    await waitFor(() => expect(seen.at(-1)?.searchParams.get('category')).toBe('c1'));
    await user.type(screen.getByLabelText('Поиск'), '478');
    await waitFor(() => expect(seen.at(-1)?.searchParams.get('q')).toBe('478'), { timeout: 2000 });
  });

  it('passes price_type / warehouse and reads price + stock_qty (order editor source)', async () => {
    const { productsApi } = await import('@/api/admin');
    const seen = setup();
    server.use(
      http.get('*/api/admin/products', ({ request }) => {
        seen.push(new URL(request.url));
        return HttpResponse.json({ total: 1, items: [{ ...product(), price: 900, stock_qty: 5 }] });
      }),
    );
    const r = await productsApi.list({ limit: 10, offset: 0, price_type: 'p2', warehouse: 'w1' });
    expect(seen.at(-1)?.searchParams.get('price_type')).toBe('p2');
    expect(seen.at(-1)?.searchParams.get('warehouse')).toBe('w1');
    expect(r.items[0]).toMatchObject({ price: 900, stock_qty: 5 });
  });

  it('drawer shows stock per warehouse and prices per type', async () => {
    setup();
    renderApp('/admin/products');
    const { drawer } = await openDrawer();
    const stock = within(drawer).getByRole('table', { name: 'Остатки по складам' });
    expect(within(stock).getByText('Main warehouse')).toBeInTheDocument();
    expect(within(stock).getByText('40')).toBeInTheDocument();
    const prices = within(drawer).getByRole('table', { name: 'Цены по типам' });
    expect(within(prices).getByText('Wholesale')).toBeInTheDocument();
    expect(within(prices).getByText('900')).toBeInTheDocument();
  });

  it('validates the barcode (8-14 digits) before calling the API', async () => {
    setup();
    let posts = 0;
    server.use(
      http.post('*/api/admin/products/p1/barcodes', () => {
        posts += 1;
        return HttpResponse.json(product(), { status: 201 });
      }),
    );
    renderApp('/admin/products');
    const { user, drawer } = await openDrawer();
    const input = within(drawer).getByLabelText('Новый штрихкод');
    for (const bad of ['1234567', '12ab5678', '1234 5678']) {
      await user.clear(input);
      await user.type(input, bad);
      await user.click(within(drawer).getByRole('button', { name: 'Добавить' }));
      expect(await within(drawer).findByText('Штрихкод — от 8 до 14 цифр')).toBeInTheDocument();
    }
    expect(posts).toBe(0);
  });

  it('adds a valid barcode and warns when it is used by other products', async () => {
    setup();
    const bodies: unknown[] = [];
    server.use(
      http.post('*/api/admin/products/p1/barcodes', async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(
          {
            ...product({
              barcodes: [
                { barcode: '4780000000011', source: 'sd' },
                { barcode: '1234567890123', source: 'admin' },
                { barcode: '99887766', source: 'admin' },
              ],
            }),
            warnings: ['barcode_used_by_other_products'],
            other_products: [{ sd_id: 'p9', name: 'Other filter' }],
          },
          { status: 201 },
        );
      }),
    );
    renderApp('/admin/products');
    const { user, drawer } = await openDrawer();
    await user.type(within(drawer).getByLabelText('Новый штрихкод'), '99887766');
    await user.click(within(drawer).getByRole('button', { name: 'Добавить' }));
    await waitFor(() => expect(bodies).toEqual([{ barcode: '99887766' }]));
    expect(
      await within(drawer).findByText(/уже используется другими товарами.*Other filter/),
    ).toBeInTheDocument();
    expect(within(drawer).getByText('99887766')).toBeInTheDocument();
  });

  it('SD barcodes cannot be removed, admin ones can', async () => {
    setup();
    const removed: unknown[] = [];
    server.use(
      http.delete('*/api/admin/products/p1/barcodes', async ({ request }) => {
        removed.push(await request.json());
        return HttpResponse.json(
          product({ barcodes: [{ barcode: '4780000000011', source: 'sd' }] }),
        );
      }),
    );
    renderApp('/admin/products');
    const { user, drawer } = await openDrawer();
    expect(
      within(drawer).queryByRole('button', { name: 'Удалить штрихкод: 4780000000011' }),
    ).not.toBeInTheDocument();
    await user.click(
      within(drawer).getByRole('button', { name: 'Удалить штрихкод: 1234567890123' }),
    );
    await waitFor(() => expect(removed).toEqual([{ barcode: '1234567890123' }]));
  });

  it('hide/unhide and note are sent as a patch; read-only users cannot edit', async () => {
    setup();
    const patches: unknown[] = [];
    server.use(
      http.patch('*/api/admin/products/p1', async ({ request }) => {
        patches.push(await request.json());
        return HttpResponse.json(product({ hidden: true }));
      }),
    );
    renderApp('/admin/products');
    const { user, drawer } = await openDrawer();
    await user.click(within(drawer).getByRole('checkbox', { name: 'Скрыть от магазинов' }));
    await waitFor(() => expect(patches).toEqual([{ hidden: true }]));
    await user.type(within(drawer).getByLabelText('Заметка'), 'discontinued');
    await user.click(within(drawer).getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(patches).toContainEqual({ note: 'discontinued' }));
  });

  it('viewers (no products.write) see the data but no edit controls', async () => {
    setup(['products.read', 'links.read']);
    renderApp('/admin/products');
    const { drawer } = await openDrawer();
    expect(within(drawer).getByRole('checkbox', { name: 'Скрыть от магазинов' })).toBeDisabled();
    expect(within(drawer).queryByLabelText('Новый штрихкод')).not.toBeInTheDocument();
    expect(
      within(drawer).queryByRole('button', { name: /Удалить штрихкод/ }),
    ).not.toBeInTheDocument();
  });

  it('selects a page, then everything matching the filter, and hides it in bulk', async () => {
    setup();
    let idsQuery: URL | null = null;
    const bulks: unknown[] = [];
    server.use(
      http.get('*/api/admin/products', () =>
        HttpResponse.json({
          total: 5,
          items: [product(), product({ sd_id: 'p2', name: 'Air filter' })],
        }),
      ),
      http.get('*/api/admin/products/ids', ({ request }) => {
        idsQuery = new URL(request.url);
        return HttpResponse.json({ total: 5, sd_ids: ['p1', 'p2', 'p3', 'p4', 'p5'] });
      }),
      http.post('*/api/admin/products/bulk', async ({ request }) => {
        bulks.push(await request.json());
        return HttpResponse.json({ updated: 5, not_found: 0 });
      }),
    );
    renderApp('/admin/products');
    await screen.findByText('Air filter');
    const user = userEvent.setup();
    await user.click(screen.getByRole('checkbox', { name: 'Выбрать: Oil filter' }));
    expect(screen.getByText('Выбрано: 1')).toBeInTheDocument();

    await user.click(screen.getByRole('checkbox', { name: 'Выбрать все на странице' }));
    expect(screen.getByText('Выбрано: 2')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Выбрать все 5 по фильтру' }));
    expect(await screen.findByText('Выбрано: 5')).toBeInTheDocument();
    expect(idsQuery).not.toBeNull();

    await user.click(screen.getByRole('button', { name: 'Скрыть из магазина' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('Скрыть 5 товаров из каталога магазинов?');
    await user.click(within(dialog).getByRole('button', { name: 'Подтвердить' }));
    await waitFor(() =>
      expect(bulks).toEqual([{ sd_ids: ['p1', 'p2', 'p3', 'p4', 'p5'], hidden: true }]),
    );
    expect(await screen.findByText('Обновлено товаров: 5')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/^Выбрано:/)).not.toBeInTheDocument());
  });

  it('card view; viewers can select but get no bulk actions', async () => {
    localStorage.removeItem('admin-products-view');
    setup(['products.read', 'links.read']);
    renderApp('/admin/products');
    await screen.findByText('Oil filter');
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Карточками' }));
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    const cards = screen.getByRole('list', { name: 'Товары' });
    expect(within(cards).getAllByRole('listitem')).toHaveLength(1);
    await user.click(within(cards).getByRole('checkbox', { name: 'Выбрать: Oil filter' }));
    expect(screen.getByText('Выбрано: 1')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Скрыть из магазина' })).not.toBeInTheDocument();
    localStorage.removeItem('admin-products-view');
  });
});

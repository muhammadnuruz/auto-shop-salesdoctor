import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SETTINGS_KEY } from '@/features/orders/useOrderSettings';
import { catalogItem, ORDER_ID, orderFixture, orderLine } from '@/test/fixtures';
import { FakeOrders } from '@/test/orderServer';
import { server } from '@/test/server';
import { renderApp } from '@/test/utils';

const beeps = vi.hoisted(() => ({ ok: vi.fn(), error: vi.fn() }));
vi.mock('@/lib/beep', () => ({ beepOk: beeps.ok, beepError: beeps.error }));

const PATH = `/s/demo-shop/orders/${ORDER_ID}`;

beforeEach(() => {
  beeps.ok.mockClear();
  beeps.error.mockClear();
});

const withLine = (over = {}) => ({
  lines: [orderLine(over)],
});

async function open(fake: FakeOrders, ready = 'scanner-input') {
  fake.install();
  renderApp(PATH);
  await screen.findByTestId(ready);
}

describe('order screen: scanner', () => {
  it('adds a scanned product (+1 piece) with the order version and beeps', async () => {
    const fake = new FakeOrders();
    await open(fake);
    const user = userEvent.setup();
    await user.type(screen.getByTestId('scanner-input'), '4780000000011{Enter}');

    const row = await screen.findByTestId('order-row');
    expect(within(row).getByText('Oil filter')).toBeInTheDocument();
    expect(fake.callsTo('POST', '/lines/scan')[0]?.body).toEqual({
      version: 1,
      barcode: '4780000000011',
    });
    expect(beeps.ok).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('scanner-input')).toHaveValue('');
    // the server-recalculated total is shown
    await waitFor(() => expect(screen.getByTestId('grand-total')).toHaveTextContent('1 000 сум'));
  });

  it('asks which product to use when the barcode is ambiguous', async () => {
    const a = catalogItem({ product_ref: 'pa', name: 'Filter A' });
    const b = catalogItem({ product_ref: 'pb', name: 'Filter B' });
    const fake = new FakeOrders({}, [a, b]);
    fake.ambiguous['999'] = [a, b];
    await open(fake);
    const user = userEvent.setup();
    await user.type(screen.getByTestId('scanner-input'), '999{Enter}');

    const dialog = await screen.findByRole('dialog', { name: 'Выберите товар' });
    await user.click(within(dialog).getByText('Filter B'));
    await waitFor(() => expect(fake.callsTo('POST', '/lines/scan')).toHaveLength(2));
    expect(fake.callsTo('POST', '/lines/scan')[1]?.body).toEqual({
      version: 1,
      product_ref: 'pb',
    });
    expect(await screen.findByText('Filter B', { selector: 'p' })).toBeInTheDocument();
  });

  it('shows an error toast and error beep for an unknown barcode', async () => {
    const fake = new FakeOrders();
    await open(fake);
    const user = userEvent.setup();
    await user.type(screen.getByTestId('scanner-input'), '000111{Enter}');
    expect(await screen.findByText('Штрихкод 000111 не найден')).toBeInTheDocument();
    expect(beeps.error).toHaveBeenCalledTimes(1);
    expect(beeps.ok).not.toHaveBeenCalled();
    expect(screen.queryByTestId('order-row')).not.toBeInTheDocument();
  });

  it('keeps the scanner muted when sound is off in settings', async () => {
    localStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({ sound: false, autoFocus: false, columns: {} }),
    );
    const fake = new FakeOrders();
    await open(fake);
    const user = userEvent.setup();
    await user.type(screen.getByTestId('scanner-input'), '4780000000011{Enter}');
    await screen.findByTestId('order-row');
    expect(beeps.ok).not.toHaveBeenCalled();
  });
});

describe('order screen: editing', () => {
  it('debounces stepper clicks into one request and retries once on version_conflict', async () => {
    const fake = new FakeOrders(withLine({ pieces: 1 }));
    await open(fake);
    const user = userEvent.setup();
    const plus = await screen.findByRole('button', { name: 'В штуках: Oil filter +' });
    await user.click(plus);
    await user.click(plus);
    await user.click(plus);
    // instant local feedback, nothing sent yet
    expect(screen.getByRole('textbox', { name: 'В штуках: Oil filter' })).toHaveValue('4');
    expect(screen.getByTestId('save-indicator')).toHaveAttribute('data-state', 'saving');

    await waitFor(() => expect(fake.callsTo('PATCH', '/lines/p1')).toHaveLength(1), {
      timeout: 3000,
    });
    expect(fake.callsTo('PATCH', '/lines/p1')[0]?.body).toEqual({ version: 1, pieces: 4 });
    await waitFor(() =>
      expect(screen.getByTestId('save-indicator')).toHaveAttribute('data-state', 'saved'),
    );
    expect(screen.getByTestId('save-indicator')).toHaveTextContent('Сохранено');

    // someone else changed the order: 409 -> refetch -> same intent re-applied with the new version
    fake.conflicts = 1;
    const getsBefore = fake.callsTo('GET', `/orders/${ORDER_ID}`).length;
    await user.click(plus);
    await waitFor(() => expect(fake.callsTo('PATCH', '/lines/p1')).toHaveLength(3), {
      timeout: 3000,
    });
    const patches = fake.callsTo('PATCH', '/lines/p1');
    expect(patches[1]?.body).toEqual({ version: 2, pieces: 5 });
    expect(patches[2]?.body).toEqual({ version: 3, pieces: 5 });
    expect(fake.callsTo('GET', `/orders/${ORDER_ID}`).length).toBeGreaterThan(getsBefore);
    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: 'В штуках: Oil filter' })).toHaveValue('5'),
    );
  });

  it('disables block steppers for products without a pack size', async () => {
    const fake = new FakeOrders(withLine({ pack_quantity: null }));
    await open(fake);
    expect(await screen.findByRole('textbox', { name: 'В блоках: Oil filter' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'В блоках: Oil filter +' })).toBeDisabled();
    expect(screen.getByRole('textbox', { name: 'В штуках: Oil filter' })).toBeEnabled();
  });

  it('highlights over-stock rows and keeps the warnings visible', async () => {
    const fake = new FakeOrders({
      lines: [
        orderLine({ pieces: 80, stock: 50 }),
        orderLine({
          product_ref: 'p2',
          name: 'No price',
          price: undefined,
          warnings: ['no_price'],
        }),
      ],
    });
    await open(fake);
    const rows = await screen.findAllByTestId('order-row');
    expect(rows[0]).toHaveAttribute('data-over', 'true');
    expect(rows[1]).not.toHaveAttribute('data-over');
    expect(within(rows[1] as HTMLElement).getByText('Нет цены')).toBeInTheDocument();
    const user = userEvent.setup();
    await user.hover(within(rows[0] as HTMLElement).getByText('50'));
    expect(await screen.findByText('Заказано 80, на складе только 50')).toBeInTheDocument();
  });

  it('removes a line and filters the table client-side', async () => {
    const fake = new FakeOrders({
      lines: [orderLine(), orderLine({ product_ref: 'p2', name: 'Brake pad' })],
    });
    await open(fake);
    const user = userEvent.setup();
    await screen.findAllByTestId('order-row');
    await user.type(screen.getByLabelText('Поиск в таблице'), 'brake');
    expect(screen.getAllByTestId('order-row')).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: 'Убрать товар: Brake pad' }));
    await waitFor(() => expect(fake.callsTo('DELETE', '/lines/p2')).toHaveLength(1), {
      timeout: 3000,
    });
    await waitFor(() =>
      expect(screen.getByText('Ничего не найдено в таблице')).toBeInTheDocument(),
    );
  });

  it('sends header changes (price type) with the version and keeps fixed fields disabled', async () => {
    const fake = new FakeOrders({ price_type: null });
    await open(fake);
    const user = userEvent.setup();
    expect(screen.getByRole('combobox', { name: 'Склад' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    await user.click(screen.getByRole('combobox', { name: 'Тип цены' }));
    await user.click(await screen.findByRole('option', { name: 'Wholesale' }));
    await waitFor(() => expect(fake.callsTo('PATCH', `/orders/${ORDER_ID}`)).toHaveLength(1), {
      timeout: 3000,
    });
    expect(fake.callsTo('PATCH', `/orders/${ORDER_ID}`)[0]?.body).toEqual({
      price_type: 'p2',
      version: 1,
    });
  });

  it('persists column visibility in localStorage', async () => {
    const fake = new FakeOrders(withLine());
    await open(fake);
    const user = userEvent.setup();
    const table = await screen.findByRole('table', { name: 'Товары заказа' });
    expect(within(table).getByRole('columnheader', { name: 'Категория' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Настройки' }));
    await user.click(screen.getByRole('checkbox', { name: 'Категория' }));
    await waitFor(() =>
      expect(
        within(table).queryByRole('columnheader', { name: 'Категория' }),
      ).not.toBeInTheDocument(),
    );
    expect(JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? '{}').columns.category).toBe(false);
  });
});

describe('order screen: submit', () => {
  const submitFlow = async (fake: FakeOrders) => {
    await open(fake);
    const user = userEvent.setup();
    await screen.findByTestId('order-row');
    await user.click(screen.getByRole('button', { name: 'Отправить заказ' }));
    const dialog = await screen.findByRole('dialog', { name: 'Отправить заказ?' });
    expect(within(dialog).getByText(/Позиций: 1/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Отправить' }));
    return user;
  };

  it('sent: shows the SD number with "Новый заказ" / "Повторить"', async () => {
    const fake = new FakeOrders(withLine());
    await submitFlow(fake);
    expect(await screen.findByTestId('sd-number')).toHaveTextContent('SD-777');
    expect(fake.callsTo('POST', '/submit')[0]?.body).toEqual({ version: 1 });
    expect(screen.getByRole('button', { name: 'Новый заказ' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeInTheDocument();
  });

  it('outcome_unknown: shows the processing state and polls until SD answers', async () => {
    const fake = new FakeOrders(withLine());
    fake.submit = () => {
      fake.order = { ...fake.order, state: 'outcome_unknown', editable: false };
      return HttpResponse.json(fake.order, { status: 202 });
    };
    await submitFlow(fake);
    expect(await screen.findByTestId('processing-panel')).toHaveTextContent('Заказ обрабатывается');
    const before = fake.callsTo('GET', `/orders/${ORDER_ID}`).length;
    fake.sent(); // SD confirmed in the meantime
    expect(
      await screen.findByText('SD SD-777', { exact: false }, { timeout: 6000 }),
    ).toBeInTheDocument();
    expect(fake.callsTo('GET', `/orders/${ORDER_ID}`).length).toBeGreaterThan(before);
    expect(screen.queryByTestId('processing-panel')).not.toBeInTheDocument();
  }, 12000);

  it('422 validation_failed: maps errors inline per line and as header problems', async () => {
    const fake = new FakeOrders(withLine({ pieces: 5 }));
    fake.submit = () =>
      HttpResponse.json(
        {
          detail: {
            code: 'validation_failed',
            errors: [
              { product_ref: 'p1', code: 'over_stock', available: 2, requested: 5 },
              { code: 'min_order_sum', min: 100000, sum: 5000 },
              { field: 'warehouse', code: 'required' },
            ],
          },
        },
        { status: 422 },
      );
    await submitFlow(fake);
    const row = await screen.findByTestId('order-row');
    expect(
      within(row).getByText('Недостаточно на складе: доступно 2, заказано 5'),
    ).toBeInTheDocument();
    const header = await screen.findByTestId('header-problems');
    expect(
      within(header).getByText('Сумма заказа (5000) меньше минимальной (100000)'),
    ).toBeInTheDocument();
    expect(within(header).getByText('Склад: обязательное поле')).toBeInTheDocument();
    // still editable, no success screen
    expect(screen.queryByTestId('sent-panel')).not.toBeInTheDocument();
  });

  it('422 sd_rejected: shows the SD message and per-line SD errors', async () => {
    const fake = new FakeOrders(withLine());
    fake.submit = () =>
      HttpResponse.json(
        {
          detail: {
            code: 'sd_rejected',
            state: 'failed',
            message: 'Sales Doctor rejected the order',
            errors: [{ product_ref: 'p1', message: 'Product is blocked in SD' }],
          },
        },
        { status: 422 },
      );
    await submitFlow(fake);
    expect(await screen.findByText('Product is blocked in SD')).toBeInTheDocument();
    expect(screen.getByText('Sales Doctor отклонил заказ')).toBeInTheDocument();
  });

  it('502: offers a retry that submits again; 409 order_busy waits and retries', async () => {
    const fake = new FakeOrders(withLine());
    let n = 0;
    fake.submit = () => {
      n += 1;
      if (n === 1) {
        return HttpResponse.json({ detail: { code: 'sd_unavailable' } }, { status: 502 });
      }
      return HttpResponse.json(fake.sent());
    };
    const user = await submitFlow(fake);
    const alert = await screen.findByText(/Sales Doctor временно недоступен/);
    expect(alert).toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: 'Повторить' }));
    expect(await screen.findByTestId('sd-number')).toBeInTheDocument();
    expect(n).toBe(2);
  });

  it('409 order_busy: retries after a short wait', async () => {
    const fake = new FakeOrders(withLine());
    let n = 0;
    fake.submit = () => {
      n += 1;
      return n === 1
        ? HttpResponse.json({ detail: { code: 'order_busy' } }, { status: 409 })
        : HttpResponse.json(fake.sent());
    };
    await submitFlow(fake);
    expect(await screen.findByTestId('sd-number', {}, { timeout: 5000 })).toBeInTheDocument();
    expect(n).toBe(2);
  }, 12000);

  it('disables sending an empty order', async () => {
    const fake = new FakeOrders();
    await open(fake);
    expect(screen.getByRole('button', { name: 'Отправить заказ' })).toBeDisabled();
  });
});

describe('order screen: import from file', () => {
  it('previews matched / ambiguous / unmatched rows and applies the confirmed lines', async () => {
    const fake = new FakeOrders();
    fake.install();
    const applied: unknown[] = [];
    const a = catalogItem({ product_ref: 'pa', name: 'Filter A' });
    const b = catalogItem({ product_ref: 'pb', name: 'Filter B' });
    server.use(
      // (jsdom's FormData is not serialised by Node's fetch, so the multipart body is not inspected)
      http.post('*/api/shop/orders/import', () =>
        HttpResponse.json({
          rows: 4,
          matched: [{ ...catalogItem(), rows: [2], blocks: 1, pieces: 2, quantity: 12 }],
          ambiguous: [
            { row: 3, code: '999', name: null, blocks: 0, pieces: 4, candidates: [a, b] },
          ],
          unmatched: [{ row: 4, code: '000', name: null, reason: 'not_found' }],
        }),
      ),
      http.post('*/api/shop/orders/import/apply', async ({ request }) => {
        applied.push(await request.json());
        return HttpResponse.json(orderFixture({ version: 2, lines: [orderLine({ pieces: 12 })] }));
      }),
    );
    renderApp(PATH);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Импорт из файла' }));
    const dialog = await screen.findByRole('dialog', { name: 'Импорт из файла' });
    await user.upload(
      within(dialog).getByTestId('import-file'),
      new File(['code,qty\n1,2'], 'order.csv', { type: 'text/csv' }),
    );

    expect(
      await within(dialog).findByText(/Найдено: 1 · неоднозначных: 1 · не найдено: 1/),
    ).toBeInTheDocument();
    expect(within(dialog).getByText('Товар не найден')).toBeInTheDocument();
    // nothing for the ambiguous row until the user picks a candidate
    expect(within(dialog).getByRole('button', { name: 'Добавить (1)' })).toBeEnabled();
    await user.click(within(dialog).getByRole('combobox', { name: /Строка 3: 999/ }));
    await user.click(await screen.findByRole('option', { name: /Filter B/ }));
    await user.click(within(dialog).getByRole('button', { name: 'Добавить (2)' }));

    await waitFor(() => expect(applied).toHaveLength(1));
    expect(applied[0]).toEqual({
      order_id: ORDER_ID,
      version: 1,
      lines: [
        { product_ref: 'p1', blocks: 1, pieces: 2 },
        { product_ref: 'pb', blocks: 0, pieces: 4 },
      ],
    });
    expect(await screen.findByText('Товары добавлены в заказ')).toBeInTheDocument();
  });

  it('rejects unsupported file types before uploading', async () => {
    const fake = new FakeOrders();
    await open(fake);
    const user = userEvent.setup({ applyAccept: false });
    await user.click(screen.getByRole('button', { name: 'Импорт из файла' }));
    const dialog = await screen.findByRole('dialog', { name: 'Импорт из файла' });
    await user.upload(within(dialog).getByTestId('import-file'), new File(['x'], 'a.pdf'));
    expect(
      await within(dialog).findByText('Поддерживаются только файлы .xlsx и .csv'),
    ).toBeInTheDocument();
  });
});

describe('order screen: add from catalog', () => {
  it('adds several products with quantities in one go', async () => {
    const a = catalogItem({ product_ref: 'pa', name: 'Filter A' });
    const b = catalogItem({ product_ref: 'pb', name: 'Filter B', pack_quantity: null });
    const fake = new FakeOrders({}, [a, b]);
    await open(fake);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Добавить из списка' }));
    const dialog = await screen.findByRole('dialog', { name: 'Добавить из списка' });
    await user.click(await within(dialog).findByRole('button', { name: 'В штуках: Filter A +' }));
    await user.click(within(dialog).getByRole('button', { name: 'В блоках: Filter A +' }));
    await user.click(within(dialog).getByRole('button', { name: 'В штуках: Filter B +' }));
    await user.click(within(dialog).getByRole('button', { name: 'Добавить (2)' }));

    await waitFor(() => expect(fake.callsTo('PATCH', '/lines/')).toHaveLength(2), {
      timeout: 3000,
    });
    const bodies = Object.fromEntries(
      fake.callsTo('PATCH', '/lines/').map((c) => [c.path, c.body as Record<string, unknown>]),
    );
    expect(bodies['/lines/pa']).toMatchObject({ blocks: 1, pieces: 1 });
    expect(bodies['/lines/pb']).toMatchObject({ blocks: 0, pieces: 1 });
  });

  it('switches to the card view, remembers it, and adds from a card', async () => {
    localStorage.removeItem('catalog-view');
    const a = catalogItem({ product_ref: 'pa', name: 'Filter A' });
    const fake = new FakeOrders({}, [a]);
    await open(fake);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Добавить из списка' }));
    const dialog = await screen.findByRole('dialog', { name: 'Добавить из списка' });
    expect(await within(dialog).findByRole('table')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Списком' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    await user.click(within(dialog).getByRole('button', { name: 'Карточками' }));
    expect(within(dialog).queryByRole('table')).not.toBeInTheDocument();
    const cards = within(dialog).getByRole('list', { name: 'Добавить из списка' });
    expect(within(cards).getAllByRole('listitem')).toHaveLength(1);
    expect(localStorage.getItem('catalog-view')).toBe('grid');

    await user.click(within(cards).getByRole('button', { name: 'В штуках: Filter A +' }));
    await user.click(within(dialog).getByRole('button', { name: 'Добавить (1)' }));
    await waitFor(() => expect(fake.callsTo('PATCH', '/lines/')).toHaveLength(1), {
      timeout: 3000,
    });
    localStorage.removeItem('catalog-view');
  });
});

describe('order screen: select all in the catalog', () => {
  it('picks every in-stock product (1 piece each) and adds them in one bulk request', async () => {
    const items = Array.from({ length: 12 }, (_, i) =>
      catalogItem({ product_ref: `p${i}`, name: `Item ${i}`, stock: i === 3 ? 0 : 50 }),
    );
    const fake = new FakeOrders({}, items);
    await open(fake);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Добавить из списка' }));
    const dialog = await screen.findByRole('dialog', { name: 'Добавить из списка' });
    await within(dialog).findByText('Item 0');
    await user.click(within(dialog).getAllByRole('checkbox', { name: 'Выбрать все' })[0]!);
    expect(await within(dialog).findByText('Выбрано: 11')).toBeInTheDocument();
    expect(within(dialog).getByRole('alert')).toHaveTextContent('без остатка пропущено: 1');
    expect(within(dialog).getByRole('checkbox', { name: 'Выбрать: Item 3' })).not.toBeChecked();

    await user.click(within(dialog).getByRole('checkbox', { name: 'Выбрать: Item 0' }));
    expect(within(dialog).getByText('Выбрано: 10')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Добавить (10)' }));

    await waitFor(() => expect(fake.callsTo('POST', '/import/apply')).toHaveLength(1));
    const body = fake.callsTo('POST', '/import/apply')[0]!.body as {
      lines: { product_ref: string; blocks: number; pieces: number }[];
    };
    expect(body.lines).toHaveLength(10);
    expect(body.lines.every((l) => l.blocks === 0 && l.pieces === 1)).toBe(true);
    expect(body.lines.map((l) => l.product_ref)).not.toContain('p3');
    expect(fake.callsTo('PATCH', '/lines/')).toHaveLength(0);
  });

  it('desktop: the total + submit bar is sticky', async () => {
    const fake = new FakeOrders({ lines: [orderLine()] });
    await open(fake);
    const bar = await screen.findByTestId('total-bar');
    expect(bar).toHaveStyle({ position: 'sticky', bottom: '0px' });
    expect(within(bar).getByRole('button', { name: 'Отправить заказ' })).toBeInTheDocument();
  });
});

describe('order screen: layouts', () => {
  const mobileMedia = () => {
    const original = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      matches: query.includes('max-width'),
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    })) as typeof window.matchMedia;
    return () => {
      window.matchMedia = original;
    };
  };

  it('mobile: card list instead of table, big scanner, sticky total + submit bar', async () => {
    const restore = mobileMedia();
    try {
      const fake = new FakeOrders({
        lines: [orderLine(), orderLine({ product_ref: 'p2', name: 'Brake pad' })],
        totals: { qty: 2, sum: 2000, volume: 0, lines_count: 2 },
      });
      await open(fake);
      expect(await screen.findAllByTestId('order-card')).toHaveLength(2);
      expect(screen.queryByTestId('order-row')).not.toBeInTheDocument();
      expect(screen.queryByRole('table', { name: 'Товары заказа' })).not.toBeInTheDocument();
      const bar = screen.getByTestId('sticky-bar');
      expect(within(bar).getByRole('button', { name: 'Отправить заказ' })).toBeInTheDocument();
      expect(within(bar).getByTestId('grand-total')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Сканировать камерой' })).toBeInTheDocument();
    } finally {
      restore();
    }
  });

  it('desktop: table with all columns and no camera button', async () => {
    const fake = new FakeOrders(withLine());
    await open(fake);
    const table = await screen.findByRole('table', { name: 'Товары заказа' });
    for (const name of [
      '#',
      'Фото',
      'Товар',
      'Категория',
      'Цена',
      'На складе',
      'В блоках',
      'В штуках',
      'Сумма',
      'Комментарий',
    ]) {
      expect(within(table).getByRole('columnheader', { name })).toBeInTheDocument();
    }
    expect(screen.queryByRole('button', { name: 'Сканировать камерой' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('sticky-bar')).not.toBeInTheDocument();
  });

  it('virtualizes the table above 200 rows', async () => {
    const lines = Array.from({ length: 250 }, (_, i) =>
      orderLine({ product_ref: `p${i}`, name: `Product ${i}` }),
    );
    const fake = new FakeOrders({ lines });
    await open(fake);
    const rows = await screen.findAllByTestId('order-row');
    expect(rows.length).toBeLessThan(80);
    expect(rows.length).toBeGreaterThan(5);
    expect(document.querySelector('[data-virtual="true"]')).not.toBeNull();
  });

  it('moves to the next row with Enter in a quantity field', async () => {
    const fake = new FakeOrders({
      lines: [orderLine(), orderLine({ product_ref: 'p2', name: 'Brake pad' })],
    });
    await open(fake);
    const user = userEvent.setup();
    await screen.findAllByTestId('order-row');
    await user.click(screen.getByRole('textbox', { name: 'В штуках: Oil filter' }));
    await user.keyboard('{Enter}');
    expect(screen.getByRole('textbox', { name: 'В штуках: Brake pad' })).toHaveFocus();
  });
});

describe('new order + read-only order', () => {
  it('/order/new creates a draft once and continues on its page', async () => {
    const fake = new FakeOrders();
    fake.install();
    renderApp('/s/demo-shop/order/new');
    await screen.findByTestId('scanner-input');
    expect(fake.callsTo('POST', '/orders')).toHaveLength(1);
  });

  it('too many drafts: shows the server reason with a way out', async () => {
    new FakeOrders().install();
    server.use(
      http.post('*/api/shop/orders', () =>
        HttpResponse.json({ detail: { code: 'too_many_drafts' } }, { status: 409 }),
      ),
    );
    renderApp('/s/demo-shop/order/new');
    expect(
      await screen.findByText('Слишком много открытых черновиков. Отправьте или удалите ненужные.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'К списку заказов' })).toBeInTheDocument();
  });

  it('a sent order is read-only and can be repeated or cancelled while status is "Новый"', async () => {
    const fake = new FakeOrders({
      state: 'sent',
      sd_status: 1,
      sd_id: 'SD-5',
      editable: false,
      ...withLine(),
    });
    await open(fake, 'order-row');
    const user = userEvent.setup();
    expect(await screen.findByRole('textbox', { name: 'В штуках: Oil filter' })).toBeDisabled();
    expect(screen.queryByTestId('scanner-input')).not.toBeInTheDocument();
    const cancelled: unknown[] = [];
    server.use(
      http.post(`*/api/shop/orders/${ORDER_ID}/cancel`, async ({ request }) => {
        cancelled.push(await request.json());
        fake.order = { ...fake.order, state: 'cancelled', sd_status: 5, version: 2 };
        return HttpResponse.json(fake.order);
      }),
    );
    await user.click(screen.getByRole('button', { name: 'Отменить заказ' }));
    const dialog = await screen.findByRole('dialog', { name: 'Отменить заказ?' });
    await user.click(within(dialog).getByRole('button', { name: 'Отменить заказ' }));
    await waitFor(() => expect(cancelled).toEqual([{ version: 1 }]));
    expect(await screen.findByText('Заказ отменён')).toBeInTheDocument();
  });

  it('a shipped order cannot be cancelled', async () => {
    const fake = new FakeOrders({ state: 'sent', sd_status: 2, editable: false, ...withLine() });
    await open(fake, 'order-row');
    await screen.findByRole('textbox', { name: 'В штуках: Oil filter' });
    expect(screen.queryByRole('button', { name: 'Отменить заказ' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeInTheDocument();
  });
});

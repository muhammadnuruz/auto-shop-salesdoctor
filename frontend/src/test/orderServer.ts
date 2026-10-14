import { http, HttpResponse } from 'msw';
import type { CatalogItem, Order, OrderLine } from '@/api/types';
import { server } from './server';
import { catalogItem, ORDER_ID, orderFixture, orderLine, shopMe } from './fixtures';

export interface Call {
  method: string;
  path: string;
  body: unknown;
}

/** Stateful in-memory fake of /api/shop/orders for the order-screen tests. */
export class FakeOrders {
  order: Order;
  calls: Call[] = [];
  catalog: CatalogItem[];
  /** respond 409 version_conflict to the next N mutating calls */
  conflicts = 0;
  /** product_ref -> candidates for ambiguous barcodes */
  ambiguous: Record<string, CatalogItem[]> = {};
  submit: () => Response | Promise<Response> = () => HttpResponse.json(this.sent());

  constructor(order: Partial<Order> = {}, catalog: CatalogItem[] = [catalogItem()]) {
    this.order = orderFixture(order);
    this.catalog = catalog;
    this.recalc();
  }

  sent(): Order {
    this.order = { ...this.order, state: 'sent', sd_status: 1, sd_id: 'SD-777', editable: false };
    return this.order;
  }

  private recalc() {
    const lines = this.order.lines.map((l, i) => {
      const quantity = l.blocks * (l.pack_quantity ?? 0) + l.pieces;
      const over = l.stock != null && quantity > l.stock;
      return {
        ...l,
        position: i + 1,
        quantity,
        line_sum: quantity * (l.price ?? 0),
        warnings: over ? ['over_stock'] : l.warnings.filter((w) => w !== 'over_stock'),
      };
    });
    const sum = lines.reduce((a, l) => a + l.line_sum, 0);
    const qty = lines.reduce((a, l) => a + l.quantity, 0);
    this.order = {
      ...this.order,
      lines,
      total_sum: sum,
      total_qty: qty,
      lines_count: lines.length,
      totals: { qty, sum, volume: 0, lines_count: lines.length },
      categories: lines.length ? [{ category: 'c1', name: 'Filters', qty, volume: 0, sum }] : [],
    };
  }

  private bump() {
    this.order = { ...this.order, version: this.order.version + 1 };
    this.recalc();
    return HttpResponse.json(this.order);
  }

  private line(ref: string): OrderLine {
    const item = this.catalog.find((c) => c.product_ref === ref) ?? catalogItem();
    return orderLine({
      product_ref: item.product_ref,
      name: item.name,
      pack_quantity: item.pack_quantity,
      price: item.price ?? undefined,
      stock: item.stock,
      barcodes: item.barcodes,
      blocks: 0,
      pieces: 0,
    });
  }

  private conflict() {
    if (this.conflicts > 0) {
      this.conflicts -= 1;
      return HttpResponse.json(
        { detail: { code: 'version_conflict', current_version: this.order.version + 1 } },
        { status: 409 },
      );
    }
    return null;
  }

  private note(method: string, path: string, body: unknown) {
    this.calls.push({ method, path, body });
  }

  callsTo(method: string, pathPart: string): Call[] {
    return this.calls.filter((c) => c.method === method && c.path.includes(pathPart));
  }

  install(me = shopMe()) {
    const base = '*/api/shop';
    server.use(
      http.get(`${base}/me`, () => HttpResponse.json(me)),
      http.get(`${base}/orders/${ORDER_ID}`, () => {
        this.note('GET', `/orders/${ORDER_ID}`, null);
        return HttpResponse.json(this.order);
      }),
      http.post(`${base}/orders`, async ({ request }) => {
        this.note('POST', '/orders', await request.json());
        return HttpResponse.json(this.order, { status: 201 });
      }),
      http.patch(`${base}/orders/${ORDER_ID}`, async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        this.note('PATCH', `/orders/${ORDER_ID}`, body);
        const c = this.conflict();
        if (c) {
          this.order = { ...this.order, version: this.order.version + 1 };
          return c;
        }
        const { version: _v, ...rest } = body;
        void _v;
        const o = { ...this.order } as Order & Record<string, unknown>;
        for (const [k, v] of Object.entries(rest)) {
          if (['agent', 'warehouse', 'price_type', 'trade'].includes(k)) {
            o[k] = v ? { id: v as string, name: String(v) } : null;
          } else o[k] = v;
        }
        this.order = o;
        return this.bump();
      }),
      http.patch(`${base}/orders/${ORDER_ID}/lines/:ref`, async ({ request, params }) => {
        const body = (await request.json()) as {
          version: number;
          blocks?: number;
          pieces?: number;
          comment?: string;
        };
        this.note('PATCH', `/lines/${String(params.ref)}`, body);
        const c = this.conflict();
        if (c) {
          this.order = { ...this.order, version: this.order.version + 1 };
          return c;
        }
        const ref = String(params.ref);
        const existing = this.order.lines.find((l) => l.product_ref === ref);
        const next = {
          ...(existing ?? this.line(ref)),
          ...(body.blocks !== undefined ? { blocks: body.blocks } : {}),
          ...(body.pieces !== undefined ? { pieces: body.pieces } : {}),
          ...(body.comment !== undefined ? { comment: body.comment } : {}),
        };
        const rest = this.order.lines.filter((l) => l.product_ref !== ref);
        this.order = {
          ...this.order,
          lines:
            next.blocks === 0 && next.pieces === 0
              ? rest
              : existing
                ? this.order.lines.map((l) => (l.product_ref === ref ? next : l))
                : [...rest, next],
        };
        return this.bump();
      }),
      http.delete(`${base}/orders/${ORDER_ID}/lines/:ref`, ({ params }) => {
        this.note('DELETE', `/lines/${String(params.ref)}`, null);
        this.order = {
          ...this.order,
          lines: this.order.lines.filter((l) => l.product_ref !== params.ref),
        };
        return this.bump();
      }),
      http.post(`${base}/orders/${ORDER_ID}/lines/scan`, async ({ request }) => {
        const body = (await request.json()) as {
          version: number;
          barcode?: string;
          product_ref?: string;
          pieces?: number;
        };
        this.note('POST', '/lines/scan', body);
        let ref = body.product_ref;
        if (!ref) {
          const cands = this.ambiguous[body.barcode ?? ''];
          if (cands) {
            return HttpResponse.json(
              { detail: { code: 'ambiguous_barcode', candidates: cands } },
              { status: 409 },
            );
          }
          const found = this.catalog.filter((c) => c.barcodes.includes(body.barcode ?? ''));
          if (found.length === 0) {
            return HttpResponse.json(
              { detail: { code: 'barcode_not_found', message: 'Barcode not found' } },
              { status: 404 },
            );
          }
          ref = found[0]?.product_ref;
        }
        const existing = this.order.lines.find((l) => l.product_ref === ref);
        const base0 = existing ?? this.line(ref ?? '');
        const next = { ...base0, pieces: base0.pieces + (body.pieces ?? 1) };
        this.order = {
          ...this.order,
          lines: existing
            ? this.order.lines.map((l) => (l.product_ref === ref ? next : l))
            : [...this.order.lines, next],
        };
        return this.bump();
      }),
      http.post(`${base}/orders/${ORDER_ID}/submit`, async ({ request }) => {
        this.note('POST', '/submit', await request.json());
        return this.submit();
      }),
      http.get(`${base}/catalog`, () =>
        HttpResponse.json({
          items: this.catalog,
          total: this.catalog.length,
          page: 1,
          size: 30,
          price_type: 'p1',
          warehouse: 'w1',
        }),
      ),
      http.get(`${base}/catalog/categories`, () =>
        HttpResponse.json({ items: [{ id: 'c1', name: 'Filters', products: 1 }] }),
      ),
    );
  }
}

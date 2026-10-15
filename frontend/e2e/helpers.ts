import { expect, type APIRequestContext, type Page } from '@playwright/test';

export const FAKE_SD = process.env.E2E_FAKE_SD_URL ?? 'http://127.0.0.1:19000';
export const SLUG = 'e2e-shop-link';
export const SHOP_PHONE = '902996625';
export const ADMIN_USER = 'e2e-admin';
export const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? 'e2e-Admin-Passw0rd!';
export const PRODUCT = { ref: 'd0_2784', name: 'DEMAX toothbrush', barcode: '4780000000011' };

export interface SdOrder {
  SD_id: string;
  code_1C: string;
  status: number;
  orderProducts: { product: { SD_id: string }; quantity: number }[];
}
export interface SdState {
  orders: Record<string, SdOrder>;
  stock: Record<string, Record<string, number>>;
  deleted: string[];
  calls: Record<string, number>;
}

export async function sdState(request: APIRequestContext): Promise<SdState> {
  const res = await request.get(`${FAKE_SD}/__state`);
  expect(res.ok()).toBeTruthy();
  return (await res.json()) as SdState;
}

export async function sdScript(request: APIRequestContext, setOrder: string[]): Promise<void> {
  const res = await request.post(`${FAKE_SD}/__script`, { data: { set_order: setOrder } });
  expect(res.ok()).toBeTruthy();
}

export const stockOf = (s: SdState, ref: string) => s.stock['d0_1']?.[ref] ?? 0;

export async function shopLogin(page: Page): Promise<void> {
  await page.goto(`/s/${SLUG}`);
  await page.getByLabel('Номер телефона').fill(SHOP_PHONE);
  await page.getByRole('button', { name: 'Войти' }).click();
  await expect(page).toHaveURL(new RegExp(`/s/${SLUG}/home`));
}

export async function adminLogin(page: Page): Promise<void> {
  await page.goto('/admin/login');
  await page.getByLabel('Логин').fill(ADMIN_USER);
  await page.getByLabel('Пароль', { exact: true }).fill(ADMIN_PASSWORD);
  await page.getByRole('button', { name: 'Войти' }).click();
  await expect(page.getByRole('heading', { name: 'Дашборд' })).toBeVisible();
}

const ORIGIN = new URL(process.env.E2E_BASE_URL ?? 'http://127.0.0.1:8080').origin;

/** Place an order as the test shop through the public API (fast setup for admin tests). */
export async function placeOrderViaApi(
  request: APIRequestContext,
  pieces = 2,
): Promise<{ public_id: string; code: string }> {
  const headers = { Origin: ORIGIN };
  const login = await request.post('/api/shop/auth/login', {
    headers,
    data: { slug: SLUG, phone: `+998${SHOP_PHONE}` },
  });
  expect(login.ok()).toBeTruthy();
  const csrf = ((await login.json()) as { csrf_token: string }).csrf_token;
  const h = { ...headers, 'X-CSRF-Token': csrf };
  const draft = (await (
    await request.post('/api/shop/orders', { headers: h, data: {} })
  ).json()) as {
    public_id: string;
    version: number;
  };
  const lines = await request.put(`/api/shop/orders/${draft.public_id}/lines`, {
    headers: h,
    data: {
      version: draft.version,
      lines: [{ product_ref: PRODUCT.ref, blocks: 0, pieces: String(pieces) }],
    },
  });
  expect(lines.ok()).toBeTruthy();
  const { version } = (await lines.json()) as { version: number };
  const sent = await request.post(`/api/shop/orders/${draft.public_id}/submit`, {
    headers: h,
    data: { version },
  });
  expect(sent.status()).toBe(200);
  const body = (await sent.json()) as { public_id: string; code: string; state: string };
  expect(body.state).toBe('sent');
  return { public_id: body.public_id, code: body.code };
}

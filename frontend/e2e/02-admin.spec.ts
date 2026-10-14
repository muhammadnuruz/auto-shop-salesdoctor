import { expect, test } from '@playwright/test';
import { adminLogin, placeOrderViaApi, PRODUCT, sdState } from './helpers';

test('admin: create a link and see it in the list', async ({ page }) => {
  await adminLogin(page);
  await page.getByRole('link', { name: 'Ссылки' }).click();
  await page.getByRole('link', { name: 'Новая ссылка' }).click();
  const name = `E2E link ${Date.now()}`;
  await page.getByLabel(/^Название/).fill(name);
  for (const [label, id] of [
    [/Склады: ID/, 'd0_1'],
    [/Типы цен: ID/, 'd0_2'],
    [/Направления торговли: ID/, '1'],
  ] as const) {
    const input = page.getByLabel(label).first();
    await input.fill(id);
    await input.press('Enter');
  }
  await page.getByRole('button', { name: 'Создать' }).click();
  await expect(page.getByLabel('Полная ссылка')).toHaveValue(/\/s\/[a-z0-9-]+$/);
  await expect(page.getByText('Статистика ссылки')).toBeVisible();

  await page.getByRole('link', { name: 'Ссылки' }).click();
  await expect(page.getByText(name)).toBeVisible();
});

test('admin: edit a sent order quantity → SD order updated with the same code_1C', async ({
  page,
  request,
}) => {
  const { public_id, code } = await placeOrderViaApi(request, 2);
  const before = await sdState(request);
  expect(before.orders[code]?.orderProducts[0]?.quantity).toBe(2);

  await adminLogin(page);
  await page.getByRole('link', { name: 'Заказы' }).click();
  await page.getByRole('link', { name: code }).click();
  await expect(page).toHaveURL(new RegExp(`/admin/orders/${public_id}`));

  await page.getByRole('button', { name: `В штуках: ${PRODUCT.name} +` }).click();
  await page.getByRole('button', { name: 'Сохранить и отправить в SD' }).click();
  await expect(page.getByText('Сохранено')).toBeVisible();
  await expect
    .poll(async () => (await sdState(request)).orders[code]?.orderProducts[0]?.quantity)
    .toBe(3);
  const after = await sdState(request);
  // same SD order (idempotent code_1C), one more piece reserved
  expect(after.orders[code]?.SD_id).toBe(before.orders[code]?.SD_id);
  expect(Object.keys(after.orders)).toEqual(Object.keys(before.orders));
  expect(after.stock['d0_1']?.[PRODUCT.ref]).toBe((before.stock['d0_1']?.[PRODUCT.ref] ?? 0) - 1);
});

test('admin: audit log shows the shop login and the order submit', async ({ page }) => {
  await adminLogin(page);
  await page.getByRole('link', { name: 'Аудит' }).click();
  await expect(page.getByRole('heading', { name: 'Журнал аудита' })).toBeVisible();
  await expect(page.getByText('shop.login').first()).toBeVisible();
  await expect(page.getByText('order.submit').first()).toBeVisible();
});

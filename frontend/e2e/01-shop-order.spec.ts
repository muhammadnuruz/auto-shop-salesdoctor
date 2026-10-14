import { expect, test } from '@playwright/test';
import { PRODUCT, SLUG, sdScript, sdState, shopLogin, stockOf } from './helpers';

test.afterEach(async ({ request }) => {
  await sdScript(request, []); // never leak a scripted SD failure into the next test
});

test('shop: phone login → scan → submit → order reaches SD and reserves stock', async ({
  page,
  request,
}) => {
  const before = await sdState(request);
  await shopLogin(page);
  await expect(page.getByText('Тест Дилшод')).toBeVisible();

  await page.getByRole('link', { name: 'Новый заказ' }).first().click();
  await expect(page.getByTestId('scanner-input')).toBeVisible();

  const scanner = page.getByTestId('scanner-input');
  await scanner.fill(PRODUCT.barcode);
  await scanner.press('Enter');
  const lines = page.getByRole('table', { name: 'Товары заказа' });
  await expect(lines.getByText(PRODUCT.name)).toBeVisible();
  // a second scan of the same barcode increments the line
  await scanner.fill(PRODUCT.barcode);
  await scanner.press('Enter');
  await expect(page.getByText('Сохранено')).toBeVisible();

  await page.getByRole('button', { name: 'Отправить заказ' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Отправить' }).click();
  await expect(page.getByText('Отправлен').first()).toBeVisible();

  const after = await sdState(request);
  const created = Object.values(after.orders).filter((o) => !(o.code_1C in before.orders));
  expect(created).toHaveLength(1);
  const order = created[0]!;
  expect(order.code_1C).toMatch(/^WEB-\d{6}$/);
  expect(order.status).toBe(1);
  expect(order.orderProducts).toEqual([
    expect.objectContaining({
      product: expect.objectContaining({ SD_id: PRODUCT.ref }),
      quantity: 2,
    }),
  ]);
  expect(stockOf(after, PRODUCT.ref)).toBe(stockOf(before, PRODUCT.ref) - 2);
});

test('shop: unknown phone is refused', async ({ page }) => {
  await page.goto(`/s/${SLUG}`);
  await page.getByLabel('Номер телефона').fill('933334455');
  await page.getByRole('button', { name: 'Войти' }).click();
  await expect(
    page.getByText('Магазин по этому номеру не найден или доступ запрещён.'),
  ).toBeVisible();
});

test('shop: SD validation error keeps the order editable @mobile', async ({ page, request }) => {
  await sdScript(request, ['validation']);
  await shopLogin(page);
  await page.getByRole('link', { name: 'Новый заказ' }).first().click();
  const scanner = page.getByTestId('scanner-input');
  await scanner.fill(PRODUCT.barcode);
  await scanner.press('Enter');
  await expect(page.getByText('Сохранено')).toBeVisible();
  await page.getByRole('button', { name: 'Отправить заказ' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Отправить' }).click();
  await expect(page.getByText('Sales Doctor отклонил заказ')).toBeVisible();
  await expect(page.getByText('Product is blocked')).toBeVisible();
  // nothing reached SD and the shop can fix the order and try again
  const state = await sdState(request);
  expect(Object.values(state.orders).every((o) => o.orderProducts.length > 0)).toBe(true);
  await expect(page.getByTestId('scanner-input')).toBeEnabled();
});

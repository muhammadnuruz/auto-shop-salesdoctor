import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import i18n from '@/i18n';
import { server } from '@/test/server';
import { shopMe } from '@/test/fixtures';
import { renderApp } from '@/test/utils';

const activeLink = {
  slug: 'demo-shop',
  state: 'active',
  title: 'Demo Shop',
  logo_url: null,
  color: null,
  welcome_text: 'Xush kelibsiz!',
};

/** Session state toggled by login handlers so /me behaves like the real cookie session. */
function mockShopBackend(opts: { loggedIn?: boolean } = {}) {
  const state = {
    loggedIn: opts.loggedIn ?? false,
    loginBodies: [] as unknown[],
    csrfSeen: [] as (string | null)[],
  };
  server.use(
    http.get('*/api/shop/links/:slug', ({ params }) =>
      params.slug === 'demo-shop'
        ? HttpResponse.json(activeLink)
        : HttpResponse.json({ detail: 'Not found' }, { status: 404 }),
    ),
    http.get('*/api/shop/me', () =>
      state.loggedIn
        ? HttpResponse.json(shopMe())
        : HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 }),
    ),
    http.post('*/api/shop/auth/logout', ({ request }) => {
      state.csrfSeen.push(request.headers.get('x-csrf-token'));
      state.loggedIn = false;
      return new HttpResponse(null, { status: 204 });
    }),
  );
  return state;
}

const typePhone = async (digits = '901234567') => {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText(/Номер телефона/), digits);
  return user;
};

describe('shop login flow', () => {
  it('single client: logs in and shows the home screen with formatted balance', async () => {
    const state = mockShopBackend();
    server.use(
      http.post('*/api/shop/auth/login', async ({ request }) => {
        state.loginBodies.push(await request.json());
        state.loggedIn = true;
        return HttpResponse.json({ csrf_token: 'shop-csrf' });
      }),
    );
    renderApp('/s/demo-shop');
    const user = await typePhone();
    expect(screen.getByLabelText(/Номер телефона/)).toHaveValue('90 123 45 67');
    await user.click(screen.getByRole('button', { name: 'Войти' }));

    expect(await screen.findByRole('heading', { name: 'Bozor Market' })).toBeInTheDocument();
    expect(state.loginBodies).toEqual([{ slug: 'demo-shop', phone: '+998901234567' }]);
    expect(screen.getByTestId('balance')).toHaveTextContent('1 234 567 сум');
    expect(screen.getByText('+998 90 *** 45 67')).toBeInTheDocument();
    expect(screen.getByText(/Main warehouse/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Новый заказ/ })).toHaveAttribute(
      'href',
      '/s/demo-shop/order/new',
    );
  });

  it("shows the balance in so'm when the language is uz", async () => {
    mockShopBackend({ loggedIn: true });
    await i18n.changeLanguage('uz');
    renderApp('/s/demo-shop/home');
    expect(await screen.findByTestId('balance')).toHaveTextContent("1 234 567 so'm");
    expect(screen.getByRole('link', { name: /Yangi buyurtma/ })).toBeInTheDocument();
  });

  it('multiple clients: shows the choose step, then logs in with the selected one', async () => {
    const state = mockShopBackend();
    const selected: unknown[] = [];
    server.use(
      http.post('*/api/shop/auth/login', () =>
        HttpResponse.json({
          choose: [
            { client_ref: 'ref-1', name: 'Shop One', address: 'Addr 1' },
            { client_ref: 'ref-2', name: 'Shop Two', address: null },
          ],
        }),
      ),
      http.post('*/api/shop/auth/select', async ({ request }) => {
        selected.push(await request.json());
        state.loggedIn = true;
        return HttpResponse.json({ csrf_token: 'shop-csrf' });
      }),
    );
    renderApp('/s/demo-shop');
    const user = await typePhone();
    await user.click(screen.getByRole('button', { name: 'Войти' }));

    const list = await screen.findByRole('list', { name: 'Выберите магазин' });
    expect(within(list).getAllByRole('button')).toHaveLength(2);
    await user.click(within(list).getByRole('button', { name: /Shop Two/ }));

    expect(await screen.findByRole('heading', { name: 'Bozor Market' })).toBeInTheDocument();
    expect(selected).toEqual([{ slug: 'demo-shop', client_ref: 'ref-2' }]);
  });

  it('401: shows the generic denial message and stays on the form', async () => {
    mockShopBackend();
    server.use(
      http.post('*/api/shop/auth/login', () => HttpResponse.json({ detail: 'x' }, { status: 401 })),
    );
    renderApp('/s/demo-shop');
    const user = await typePhone();
    await user.click(screen.getByRole('button', { name: 'Войти' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Магазин по этому номеру не найден или доступ запрещён.',
    );
    expect(screen.getByLabelText(/Номер телефона/)).toBeInTheDocument();
  });

  it('429: shows the wait time from Retry-After and disables the button', async () => {
    mockShopBackend();
    server.use(
      http.post('*/api/shop/auth/login', () =>
        HttpResponse.json(
          { detail: 'Too many attempts' },
          { status: 429, headers: { 'Retry-After': '42' } },
        ),
      ),
    );
    renderApp('/s/demo-shop');
    const user = await typePhone();
    await user.click(screen.getByRole('button', { name: 'Войти' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Повторите через 42 сек.');
    const btn = screen.getByRole('button', { name: /Повторить можно через/ });
    expect(btn).toBeDisabled();
  });

  it('validates the phone before calling the API', async () => {
    mockShopBackend();
    renderApp('/s/demo-shop');
    const user = await typePhone('9012');
    await user.click(screen.getByRole('button', { name: 'Войти' }));
    expect(await screen.findByText('Введите полный номер: 9 цифр после +998')).toBeInTheDocument();
  });

  it('unknown link: shows the not-found state', async () => {
    mockShopBackend();
    renderApp('/s/nope-nope');
    expect(await screen.findByText('Ссылка не найдена')).toBeInTheDocument();
  });

  it('inactive link: shows the unavailable state without a login form', async () => {
    server.use(
      http.get('*/api/shop/links/:slug', () =>
        HttpResponse.json({ slug: 'demo-shop', state: 'inactive' }),
      ),
    );
    renderApp('/s/demo-shop');
    expect(await screen.findByText('Ссылка недоступна')).toBeInTheDocument();
    expect(screen.queryByLabelText(/Номер телефона/)).not.toBeInTheDocument();
  });

  it('redirects to the landing page when not signed in, and logs out with the CSRF header', async () => {
    const state = mockShopBackend();
    const { router, unmount } = renderApp('/s/demo-shop/home');
    expect(await screen.findByLabelText(/Номер телефона/)).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/s/demo-shop');
    unmount();

    state.loggedIn = true;
    const second = renderApp('/s/demo-shop/home');
    await screen.findByRole('heading', { name: 'Bozor Market' });
    await userEvent.setup().click(screen.getByRole('button', { name: 'Выйти' }));
    await waitFor(() => expect(second.router.state.location.pathname).toBe('/s/demo-shop'));
    expect(state.csrfSeen).toEqual(['shop-csrf']);
  });
});

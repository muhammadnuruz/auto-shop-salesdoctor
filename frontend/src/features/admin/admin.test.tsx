import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { ROLE_PERMISSIONS } from '@/lib/permissions';
import { adminUser, VIEWER_PERMS } from '@/test/fixtures';
import { server } from '@/test/server';
import { renderApp } from '@/test/utils';

const meAs = (user: ReturnType<typeof adminUser> | null) =>
  http.get('*/api/admin/auth/me', () =>
    user
      ? HttpResponse.json({ user, csrf_token: 'admin-csrf' })
      : HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 }),
  );

describe('admin login', () => {
  it('redirects unauthenticated visitors to the login page', async () => {
    server.use(meAs(null));
    const { router } = renderApp('/admin/links');
    expect(await screen.findByRole('heading', { name: 'Вход в админ-панель' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/admin/login');
  });

  it('password then TOTP step, sending the code with the same credentials', async () => {
    const bodies: Record<string, unknown>[] = [];
    let loggedIn = false;
    server.use(
      http.get('*/api/admin/auth/me', () =>
        loggedIn
          ? HttpResponse.json({ user: adminUser({ totp_enabled: true }), csrf_token: 'admin-csrf' })
          : HttpResponse.json({ detail: 'Not authenticated' }, { status: 401 }),
      ),
      http.post('*/api/admin/auth/login', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        bodies.push(body);
        if (!body.totp) return HttpResponse.json({ detail: 'totp_required' }, { status: 401 });
        if (body.totp !== '123456') return HttpResponse.json({ detail: 'bad' }, { status: 401 });
        loggedIn = true;
        return HttpResponse.json({
          csrf_token: 'admin-csrf',
          must_change_password: false,
          user: adminUser({ totp_enabled: true }),
        });
      }),
    );
    const { router } = renderApp('/admin/login');
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText('Логин'), 'root');
    await user.type(screen.getByLabelText('Пароль'), 'correct horse battery');
    await user.click(screen.getByRole('button', { name: 'Войти' }));

    expect(
      await screen.findByRole('heading', { name: 'Двухфакторная аутентификация' }),
    ).toBeInTheDocument();

    await user.type(screen.getByLabelText('Код из приложения'), '000000');
    await user.click(screen.getByRole('button', { name: 'Подтвердить' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Неверный код');

    await user.clear(screen.getByLabelText('Код из приложения'));
    await user.type(screen.getByLabelText('Код из приложения'), '123456');
    await user.click(screen.getByRole('button', { name: 'Подтвердить' }));

    await waitFor(() => expect(router.state.location.pathname).toBe('/admin'));
    expect(bodies).toEqual([
      { username: 'root', password: 'correct horse battery' },
      { username: 'root', password: 'correct horse battery', totp: '000000' },
      { username: 'root', password: 'correct horse battery', totp: '123456' },
    ]);
  });

  it('shows the generic message on bad credentials', async () => {
    server.use(
      meAs(null),
      http.post('*/api/admin/auth/login', () =>
        HttpResponse.json({ detail: 'x' }, { status: 401 }),
      ),
    );
    renderApp('/admin/login');
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText('Логин'), 'root');
    await user.type(screen.getByLabelText('Пароль'), 'nope');
    await user.click(screen.getByRole('button', { name: 'Войти' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Неверный логин или пароль');
    expect(
      screen.queryByRole('heading', { name: 'Двухфакторная аутентификация' }),
    ).not.toBeInTheDocument();
  });

  it('forces a password change when must_change_password is set', async () => {
    server.use(meAs(adminUser({ must_change_password: true })));
    const { router } = renderApp('/admin/links');
    expect(await screen.findByRole('heading', { name: 'Смена пароля' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/admin/change-password');
  });

  it('validates the new password client-side', async () => {
    server.use(meAs(adminUser({ must_change_password: true })));
    renderApp('/admin/change-password');
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText('Текущий пароль'), 'temp-password-1');
    await user.type(screen.getByLabelText('Новый пароль'), 'short');
    await user.type(screen.getByLabelText('Повторите новый пароль'), 'different');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(await screen.findByText('Пароли не совпадают')).toBeInTheDocument();
    expect(screen.getAllByText('Минимум 12 символов').length).toBeGreaterThan(0);
  });
});

describe('permission-filtered sidebar', () => {
  const sidebar = () => screen.getByRole('navigation', { name: 'Админ-панель' });

  it('superadmin sees every section', async () => {
    server.use(meAs(adminUser()));
    renderApp('/admin');
    await screen.findByTestId('whoami');
    const nav = within(sidebar());
    for (const name of [
      'Дашборд',
      'Заказы',
      'Ссылки',
      'Магазины',
      'Товары',
      'Синхронизация',
      'Администраторы',
      'Аудит',
      'Профиль',
    ])
      expect(nav.getByRole('link', { name })).toBeInTheDocument();
  });

  it('viewer does not see admins/audit and gets a no-permission screen via URL', async () => {
    const viewer = adminUser({
      id: 2,
      username: 'view',
      role: 'viewer',
      permissions: VIEWER_PERMS,
    });
    server.use(meAs(viewer));
    renderApp('/admin');
    await screen.findByTestId('whoami');
    const nav = within(sidebar());
    expect(nav.getByRole('link', { name: 'Ссылки' })).toBeInTheDocument();
    expect(nav.getByRole('link', { name: 'Синхронизация' })).toBeInTheDocument();
    expect(nav.queryByRole('link', { name: 'Администраторы' })).not.toBeInTheDocument();
    expect(nav.queryByRole('link', { name: 'Аудит' })).not.toBeInTheDocument();
  });

  it('operator has no sync/clients-write entries and is blocked from /admin/users', async () => {
    const operator = adminUser({
      id: 3,
      username: 'op',
      role: 'operator',
      permissions: [...ROLE_PERMISSIONS.operator],
    });
    server.use(meAs(operator));
    renderApp('/admin/users');
    expect(await screen.findByText('Нет доступа')).toBeInTheDocument();
    const nav = within(sidebar());
    expect(nav.queryByRole('link', { name: 'Синхронизация' })).not.toBeInTheDocument();
    expect(nav.queryByRole('link', { name: 'Администраторы' })).not.toBeInTheDocument();
    expect(nav.getByRole('link', { name: 'Заказы' })).toBeInTheDocument();
  });
});

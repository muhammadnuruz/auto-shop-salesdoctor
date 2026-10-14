import { screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { ROLE_PERMISSIONS } from '@/lib/permissions';
import { adminUser, ALL_PERMS } from '@/test/fixtures';
import { server } from '@/test/server';
import { renderApp } from '@/test/utils';

const me = (user: ReturnType<typeof adminUser>) =>
  http.get('*/api/admin/auth/me', () => HttpResponse.json({ user, csrf_token: 'c' }));

describe('admins page', () => {
  it('hides actions the current admin cannot perform', async () => {
    const actor = adminUser({
      id: 2,
      username: 'boss',
      role: 'admin',
      permissions: [...ROLE_PERMISSIONS.admin, 'admins.manage'],
    });
    const superadmin = adminUser({ id: 1, username: 'root' });
    server.use(
      me(actor),
      http.get('*/api/admin/users', () =>
        HttpResponse.json({ total: 2, items: [superadmin, { ...actor }] }),
      ),
    );
    renderApp('/admin/users');
    const row = async (name: string) =>
      (await screen.findByText(name)).closest('tr') as HTMLElement;
    const own = within(await row('boss'));
    // self: can edit / revoke, but not delete or reset own password here
    expect(own.getByRole('button', { name: /Изменить: boss/ })).toBeInTheDocument();
    expect(own.queryByRole('button', { name: /Удалить: boss/ })).not.toBeInTheDocument();
    // a superadmin is out of reach for a non-superadmin
    const root = within(await row('root'));
    expect(root.queryByRole('button')).not.toBeInTheDocument();
  });
});

describe('sync page', () => {
  const status = {
    entities: ['references', 'products'],
    latest: {
      products: {
        id: 1,
        entity: 'products',
        status: 'success',
        started_at: '2026-10-14T08:00:00Z',
        finished_at: null,
        items: 120,
        warnings: 2,
        duration_ms: 3400,
        error: null,
      },
    },
    recent: [],
  };
  it('lists the latest run per entity and offers run buttons only with sync.run', async () => {
    server.use(
      me(adminUser()),
      http.get('*/api/admin/sync/status', () => HttpResponse.json(status)),
      http.get('*/api/admin/health/sd', () =>
        HttpResponse.json(
          { status: 'error', error: 'SDTimeout', latency_ms: 5000 },
          { status: 503 },
        ),
      ),
    );
    renderApp('/admin/sync');
    expect(await screen.findByText('Справочники')).toBeInTheDocument();
    expect(screen.getByText('Не запускалась')).toBeInTheDocument();
    expect(screen.getByText('120')).toBeInTheDocument();
    expect(screen.getByText('3.4 s')).toBeInTheDocument();
    expect(await screen.findByText('Недоступен')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /Запустить/ })).toHaveLength(2);
  });

  it('hides run buttons for read-only admins', async () => {
    server.use(
      me(adminUser({ role: 'viewer', permissions: ALL_PERMS.filter((p) => p !== 'sync.run') })),
      http.get('*/api/admin/sync/status', () => HttpResponse.json(status)),
      http.get('*/api/admin/health/sd', () => HttpResponse.json({ status: 'ok', latency_ms: 80 })),
    );
    renderApp('/admin/sync');
    expect(await screen.findByText('Справочники')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Запустить/ })).not.toBeInTheDocument();
  });
});

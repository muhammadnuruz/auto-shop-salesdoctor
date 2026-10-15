import {
  createBrowserRouter,
  createMemoryRouter,
  Navigate,
  type RouteObject,
} from 'react-router-dom';
import { RouteError } from '@/components/RouteError';
import { NotFoundPage } from '@/components/NotFoundPage';
import { RequireAdmin, RequirePermission } from '@/features/admin/guards';

const page =
  <T extends { default: React.ComponentType }>(load: () => Promise<T>) =>
  async () => ({
    Component: (await load()).default,
  });
const named =
  <T,>(load: () => Promise<T>, pick: (m: T) => React.ComponentType) =>
  async () => ({
    Component: pick(await load()),
  });

export const routes: RouteObject[] = [
  { path: '/', element: <Navigate to="/admin" replace /> },

  // ---- shop ----
  {
    path: '/s/:slug',
    children: [
      { index: true, lazy: page(() => import('@/features/shop/LinkLandingPage')) },
      {
        lazy: page(() => import('@/features/shop/RequireShop')),
        children: [
          { path: 'home', lazy: page(() => import('@/features/shop/ShopHomePage')) },
          { path: 'order/new', lazy: page(() => import('@/features/shop/order/NewOrderPage')) },
          { path: 'orders', lazy: page(() => import('@/features/shop/ShopOrdersPage')) },
          { path: 'orders/:id', lazy: page(() => import('@/features/shop/order/OrderPage')) },
        ],
      },
    ],
  },

  // ---- admin ----
  { path: '/admin/login', lazy: page(() => import('@/features/admin/LoginPage')) },
  {
    path: '/admin/change-password',
    element: <RequireAdmin allowForcedChange />,
    children: [{ index: true, lazy: page(() => import('@/features/admin/ChangePasswordPage')) }],
  },
  {
    path: '/admin',
    element: <RequireAdmin />,
    children: [
      {
        lazy: page(() => import('@/features/admin/AdminLayout')),
        children: [
          {
            index: true,
            lazy: page(() => import('@/features/admin/pages/DashboardPage')),
          },
          {
            element: <RequirePermission perm="orders.read" />,
            children: [
              {
                path: 'orders',
                lazy: page(() => import('@/features/admin/orders/AdminOrdersPage')),
              },
              {
                path: 'orders/:id',
                lazy: page(() => import('@/features/admin/orders/AdminOrderDetailPage')),
              },
            ],
          },
          {
            element: <RequirePermission perm="links.read" />,
            children: [
              { path: 'links', lazy: page(() => import('@/features/admin/links/LinksListPage')) },
              {
                path: 'links/new',
                lazy: named(
                  () => import('@/features/admin/links/LinkEditPage'),
                  (m) => m.LinkCreatePage,
                ),
              },
              {
                path: 'links/:id',
                lazy: named(
                  () => import('@/features/admin/links/LinkEditPage'),
                  (m) => m.LinkEditPage,
                ),
              },
            ],
          },
          {
            element: <RequirePermission perm="clients.read" />,
            children: [
              { path: 'clients', lazy: page(() => import('@/features/admin/pages/ClientsPage')) },
            ],
          },
          {
            element: <RequirePermission perm="products.read" />,
            children: [
              {
                path: 'products',
                lazy: page(() => import('@/features/admin/products/AdminProductsPage')),
              },
            ],
          },
          {
            element: <RequirePermission perm="sync.read" />,
            children: [
              { path: 'sync', lazy: page(() => import('@/features/admin/pages/SyncPage')) },
            ],
          },
          {
            element: <RequirePermission perm="admins.manage" />,
            children: [
              { path: 'users', lazy: page(() => import('@/features/admin/pages/UsersPage')) },
            ],
          },
          {
            element: <RequirePermission perm="audit.read" />,
            children: [
              {
                path: 'audit',
                lazy: page(() => import('@/features/admin/pages/AuditPage')),
              },
            ],
          },
          {
            element: <RequirePermission perm="reports.read" />,
            children: [
              { path: 'reports', lazy: page(() => import('@/features/admin/pages/ReportsPage')) },
            ],
          },
          {
            element: <RequirePermission perm="settings.write" />,
            children: [
              {
                path: 'settings',
                lazy: page(() => import('@/features/admin/pages/SettingsPage')),
              },
            ],
          },
          { path: 'profile', lazy: page(() => import('@/features/admin/pages/ProfilePage')) },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
  { path: '*', element: <NotFoundPage /> },
];

const root: RouteObject[] = [{ errorElement: <RouteError />, children: routes }];

export const createAppRouter = () => createBrowserRouter(root);
export const createTestRouter = (initialEntries: string[]) =>
  createMemoryRouter(root, { initialEntries });

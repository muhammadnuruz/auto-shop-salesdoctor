/** TanStack Query key factory: one place, hierarchical, so invalidation is predictable. */
export const qk = {
  shop: {
    all: ['shop'] as const,
    link: (slug: string) => ['shop', 'link', slug] as const,
    me: () => ['shop', 'me'] as const,
    orders: {
      all: () => ['shop', 'orders'] as const,
      list: (p: object) => ['shop', 'orders', 'list', p] as const,
      detail: (id: string) => ['shop', 'orders', 'detail', id] as const,
    },
    catalog: (p: object) => ['shop', 'catalog', p] as const,
    categories: (priceType: string | null) => ['shop', 'categories', priceType] as const,
  },
  admin: {
    all: ['admin'] as const,
    me: () => ['admin', 'me'] as const,
    users: {
      all: () => ['admin', 'users'] as const,
      list: (p: { limit: number; offset: number }) => ['admin', 'users', 'list', p] as const,
    },
    links: {
      all: () => ['admin', 'links'] as const,
      list: (p: object) => ['admin', 'links', 'list', p] as const,
      detail: (id: number) => ['admin', 'links', 'detail', id] as const,
      stats: (id: number, days = 30) => ['admin', 'links', 'stats', id, days] as const,
      clients: (id: number, p: object) => ['admin', 'links', 'clients', id, p] as const,
    },
    clients: {
      all: () => ['admin', 'clients'] as const,
      list: (p: object) => ['admin', 'clients', 'list', p] as const,
    },
    phones: {
      all: () => ['admin', 'phones'] as const,
      list: (p: object) => ['admin', 'phones', 'list', p] as const,
    },
    refs: () => ['admin', 'refs'] as const,
    audit: {
      all: () => ['admin', 'audit'] as const,
      list: (p: object) => ['admin', 'audit', 'list', p] as const,
      facets: () => ['admin', 'audit', 'facets'] as const,
    },
    reports: (p: object) => ['admin', 'reports', p] as const,
    settings: () => ['admin', 'settings'] as const,
    dashboard: () => ['admin', 'dashboard'] as const,
    orders: {
      all: () => ['admin', 'orders'] as const,
      list: (p: object) => ['admin', 'orders', 'list', p] as const,
      detail: (id: string) => ['admin', 'orders', 'detail', id] as const,
    },
    products: {
      all: () => ['admin', 'products'] as const,
      list: (p: object) => ['admin', 'products', 'list', p] as const,
      catalog: (p: object) => ['admin', 'products', 'catalog', p] as const,
    },
    sync: {
      status: () => ['admin', 'sync', 'status'] as const,
      health: () => ['admin', 'sync', 'health'] as const,
    },
  },
};

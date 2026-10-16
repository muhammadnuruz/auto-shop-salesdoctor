import type {
  AdminOrder,
  AdminUser,
  CatalogItem,
  Invoice,
  Order,
  OrderLine,
  ShopMe,
} from '@/api/types';

export const ALL_PERMS = [
  'links.read',
  'links.write',
  'orders.read',
  'orders.write',
  'orders.cancel',
  'clients.read',
  'clients.write',
  'products.read',
  'products.write',
  'sync.read',
  'sync.run',
  'settings.write',
  'admins.manage',
  'audit.read',
  'reports.read',
];
export const VIEWER_PERMS = [
  'links.read',
  'orders.read',
  'clients.read',
  'products.read',
  'reports.read',
  'sync.read',
];

export const adminUser = (over: Partial<AdminUser> = {}): AdminUser => ({
  id: 1,
  username: 'root',
  full_name: 'Root Admin',
  role: 'superadmin',
  extra_permissions: [],
  permissions: ALL_PERMS,
  totp_enabled: false,
  active: true,
  must_change_password: false,
  last_login_at: null,
  created_at: '2026-01-01T00:00:00Z',
  ...over,
});

export const shopMe = (over: Partial<ShopMe['client']> = {}): ShopMe => ({
  client: {
    id: 7,
    name: 'Bozor Market',
    address: 'Toshkent, Chilonzor',
    phone: '+998 90 *** 45 67',
    balance: '1234567.00',
    ...over,
  },
  link: { slug: 'demo-shop', title: 'Demo Shop', logo_url: null, color: null, welcome_text: null },
  options: {
    warehouses: { mode: 'fixed', options: [{ id: 'w1', name: 'Main warehouse' }] },
    price_types: {
      mode: 'choice',
      options: [
        { id: 'p1', name: 'Retail' },
        { id: 'p2', name: 'Wholesale' },
      ],
    },
    agents: { mode: 'client_assigned', options: [{ id: 'a1', name: 'Agent Aziz' }] },
    trade_directions: { mode: 'fixed', options: [{ id: 't1', name: 'Direct' }] },
  },
  limits: {
    min_order_sum: 100000,
    max_order_sum: null,
    allow_over_stock: false,
    product_filter_mode: 'all',
  },
  csrf_token: 'shop-csrf',
  balance_by_type: [],
  last_price_type_sd_id: null,
});

// ---- orders ---------------------------------------------------------------------------------------
export const catalogItem = (over: Partial<CatalogItem> = {}): CatalogItem => ({
  product_ref: 'p1',
  code: 'C-1',
  name: 'Oil filter',
  category: 'c1',
  category_name: 'Filters',
  image_url: null,
  thumb_url: null,
  pack_quantity: 10,
  unit: 'шт',
  price: 1000,
  stock: 50,
  barcodes: ['4780000000011'],
  ...over,
});

export const orderLine = (over: Partial<OrderLine> = {}): OrderLine => {
  const item = catalogItem();
  return {
    product_ref: item.product_ref,
    name: item.name,
    code: item.code,
    category: item.category,
    category_name: item.category_name,
    unit: item.unit,
    image_url: null,
    thumb_url: null,
    pack_quantity: item.pack_quantity,
    barcodes: item.barcodes,
    blocks: 0,
    pieces: 1,
    quantity: 1,
    price: 1000,
    line_sum: 1000,
    comment: null,
    position: 1,
    stock: 50,
    warnings: [],
    ...over,
  };
};

export const ORDER_ID = '11111111-1111-4111-8111-111111111111';

export const orderFixture = (over: Partial<Order> = {}): Order => ({
  public_id: ORDER_ID,
  code: 'WEB-000001',
  state: 'draft',
  sd_status: null,
  sd_id: null,
  version: 1,
  agent: { id: 'a1', name: 'Agent Aziz' },
  warehouse: { id: 'w1', name: 'Main warehouse' },
  price_type: { id: 'p1', name: 'Retail' },
  trade: { id: 't1', name: 'Direct' },
  shipment_date: null,
  comment: '',
  total_sum: 0,
  total_qty: 0,
  lines_count: 0,
  created_by: 'shop',
  created_at: '2026-10-14T08:00:00Z',
  sent_at: null,
  sd_synced_at: null,
  last_error: null,
  editable: true,
  lines: [],
  totals: { qty: 0, sum: 0, volume: 0, lines_count: 0 },
  categories: [],
  limits: { min_order_sum: null, max_order_sum: null, allow_over_stock: false },
  problems: [],
  ...over,
});

export const adminOrderFixture = (over: Partial<AdminOrder> = {}): AdminOrder => ({
  ...orderFixture({
    state: 'sent',
    sd_status: 1,
    sd_id: 'SD-9',
    version: 3,
    editable: false,
    lines: [orderLine({ pieces: 2, quantity: 2, line_sum: 2000 })],
    totals: { qty: 2, sum: 2000, volume: 0, lines_count: 1 },
    categories: [{ category: 'c1', name: 'Filters', qty: 2, volume: 0, sum: 2000 }],
    ...over,
  }),
  client: { id: 7, sd_id: 'CL7', name: 'Bozor Market' },
  link: { id: 3, slug: 'demo-shop', name: 'Demo link' },
  created_by_admin_id: null,
  submitted_at: null,
  events: [
    {
      at: '2026-10-14T08:00:00Z',
      actor_type: 'shop',
      actor_id: 7,
      event: 'created',
      payload: { link: 'demo-shop' },
    },
    {
      at: '2026-10-14T08:05:00Z',
      actor_type: 'system',
      actor_id: null,
      event: 'sd_ok',
      payload: { sd_id: 'SD-9', status: 1 },
    },
  ],
  ...over,
});

export const adminRefs = {
  warehouses: [
    { id: 'w1', name: 'Main warehouse' },
    { id: 'w2', name: 'Second warehouse' },
  ],
  price_types: [
    { id: 'p1', name: 'Retail' },
    { id: 'p2', name: 'Wholesale' },
  ],
  agents: [{ id: 'a1', name: 'Agent Aziz' }],
  trade_directions: [{ id: 't1', name: 'Direct' }],
  categories: [{ id: 'c1', name: 'Filters' }],
  brands: [],
  territories: [{ id: '12', clients: 5 }],
  client_categories: [{ id: '3', clients: 9 }],
};

export const linkStats = (over: Record<string, unknown> = {}) => ({
  login_count: 3,
  max_logins: 10,
  active_sessions: 1,
  unique_clients: 2,
  orders: {
    all_time: { orders: 4, sum: 12000 },
    period: { days: 30, orders: 2, sum: 5000, logins: 3 },
    by_state: { sent: { orders: 2, sum: 5000 } },
  },
  daily: [
    { date: '2026-10-13', logins: 1, orders: 0, sum: 0 },
    { date: '2026-10-14', logins: 2, orders: 2, sum: 5000 },
  ],
  top_clients: [{ client_id: 7, name: 'Shop Seven', orders: 2, sum: 5000 }],
  ...over,
});

export const invoiceFixture = (over: Partial<Invoice> = {}): Invoice => ({
  source: 'sd',
  warning: null,
  number: '53146',
  sd_id: 'd0_53146',
  code: 'WEB-000001',
  sd_status: 1,
  date: '2026-10-16 09:46:06',
  shipment_date: '2026-10-17',
  client: { name: 'Мансур', phone: '+998 90 068 92 95', address: 'Shergiron mahallasi, M37' },
  agent: 'NIGORA',
  warehouse: 'Основной склад',
  price_type: 'Бот цена',
  trade: 'Parfum Brend',
  comment: null,
  lines: [
    {
      position: 1,
      product_ref: 'p1',
      name: 'Oil filter',
      thumb_url: null,
      unit: 'шт',
      pack_quantity: 10,
      blocks: 1,
      quantity: 10,
      price: 1700,
      discount: 0,
      sum: 17000,
    },
  ],
  total_qty: 10,
  total_sum: 17000,
  discount_sum: 0,
  total_due: 17000,
  ...over,
});

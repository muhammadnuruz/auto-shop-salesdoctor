/**
 * Request bodies come from the generated OpenAPI schema (schema.d.ts). The backend declares most
 * responses as bare `object`, so response shapes are modelled by hand here (see README "API gaps").
 */
import type { components } from './schema';

type S = components['schemas'];

export type LinkSettings = S['LinkSettings'];
export type LinkCreateBody = S['LinkCreate'];
export type LinkUpdateBody = S['LinkUpdate'];
export type ChoiceSetting = S['ChoiceSetting'];
export type AgentSetting = S['AgentSetting'];
export type ProductFilter = S['ProductFilter'];
export type Branding = S['Branding'];
export type ClientScope = LinkCreateBody['client_scope'] & string;
export type Role = S['UserCreate']['role'];
export type PhoneOverrideStatus = S['PhoneOverrideIn']['status'];

export interface Paged<T> {
  total: number;
  items: T[];
}

// ---- shop -------------------------------------------------------------------------------------
export interface PublicLink {
  slug: string;
  state: 'active' | 'inactive';
  title?: string | null;
  logo_url?: string | null;
  color?: string | null;
  welcome_text?: string | null;
}

export interface ChoiceOption {
  client_ref: string;
  name: string | null;
  address: string | null;
}

export interface ShopLoginResult {
  csrf_token?: string | null;
  choose?: ChoiceOption[] | null;
}

export interface NamedOption {
  id: string;
  name: string | null;
}
export interface OptionGroup {
  mode: string;
  options: NamedOption[];
}

// ---- admin ------------------------------------------------------------------------------------
export interface AdminUser {
  id: number;
  username: string;
  full_name: string;
  role: Role;
  extra_permissions: string[];
  permissions: string[];
  totp_enabled: boolean;
  active: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface AdminLoginResult {
  csrf_token: string;
  must_change_password: boolean;
  user: AdminUser;
}

export interface AdminMe {
  user: AdminUser;
  csrf_token: string;
}

export type LinkState = 'active' | 'inactive' | 'scheduled' | 'expired' | 'exhausted' | 'archived';

export interface AdminLink {
  id: number;
  slug: string;
  name: string;
  description: string | null;
  active: boolean;
  state: LinkState;
  starts_at: string | null;
  ends_at: string | null;
  max_logins: number | null;
  login_count: number;
  client_scope: ClientScope;
  scope_values: string[];
  settings: LinkSettings;
  created_by: number | null;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  /** only on create, e.g. "slug_low_entropy" */
  warnings?: string[];
}

export interface OrdersSum {
  orders: number;
  sum: number;
}

export interface LinkStatsDay {
  date: string;
  logins: number;
  orders: number;
  sum: number;
}

export interface LinkStats {
  login_count: number;
  max_logins: number | null;
  active_sessions: number;
  unique_clients: number;
  orders: {
    all_time: OrdersSum;
    period: OrdersSum & { days: number; logins: number };
    by_state: Record<string, OrdersSum>;
  };
  daily: LinkStatsDay[];
  top_clients: (OrdersSum & { client_id: number; name: string | null })[];
}

export interface LinkClient {
  id: number;
  sd_id: string;
  name: string | null;
}

export interface AdminClient {
  id: number;
  sd_id: string;
  name: string | null;
  address: string | null;
  active: boolean;
  blocked: boolean;
  blocked_reason: string | null;
}

export type PhoneStatus = 'ok' | 'suspicious' | 'blocked' | 'allowed';
export interface PhoneFlag {
  phone_key: string;
  status: PhoneStatus;
  reason: string | null;
  client_count: number;
  updated_at: string;
}

export interface SyncRun {
  id: number;
  entity: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  items: number;
  warnings: number;
  duration_ms: number;
  error: string | null;
}

export interface SyncStatus {
  entities: string[];
  latest: Record<string, SyncRun>;
  recent: SyncRun[];
}

export interface SyncResult {
  entity: string;
  status: string;
  items: number;
  warnings: number;
  error: string | null;
  duration_ms: number;
  run_id: number | null;
}

export interface SdHealth {
  status: 'ok' | 'error';
  latency_ms?: number;
  error?: string;
  message?: string;
}

export interface TotpSetup {
  secret: string;
  otpauth_uri: string;
}

// ---- admin reference data (GET /api/admin/refs) ------------------------------------------------
/** Named SD reference (active only; the endpoint never returns inactive rows). */
export interface RefItem {
  id: string;
  name: string | null;
}
/** Client-level ids whose SD names are not mirrored: shown as "id (N clients)". */
export interface RefCountItem {
  id: string;
  clients: number;
}
export type RefGroupName =
  | 'warehouses'
  | 'price_types'
  | 'agents'
  | 'trade_directions'
  | 'categories'
  | 'brands'
  | 'territories'
  | 'client_categories';
// ---- orders (shared by shop and admin) ----------------------------------------------------------
export type OrderState =
  'draft' | 'submitting' | 'sent' | 'failed' | 'outcome_unknown' | 'cancelled' | 'deleted';
export const ORDER_STATES: OrderState[] = [
  'draft',
  'submitting',
  'sent',
  'failed',
  'outcome_unknown',
  'cancelled',
  'deleted',
];

export interface OrderRef {
  id: string;
  name: string | null;
}

export type HeaderField = 'agent' | 'warehouse' | 'price_type' | 'trade';
export interface OrderHeaderPatch {
  agent?: string | null;
  warehouse?: string | null;
  price_type?: string | null;
  trade?: string | null;
  shipment_date?: string | null;
  comment?: string | null;
}

export interface CatalogCategory {
  id: string;
  name: string | null;
  products: number;
}

export interface ImportApplyLine {
  product_ref: string;
  blocks: number;
  pieces: number;
}

// ---- admin orders / dashboard / products -----------------------------------------------------------
export interface DashboardPeriod {
  orders: number;
  sum: number;
}
export type ShopMe = S['MeOut'];

export type Order = WithLastError<S['OrderOut']>;

/** `last_error` is an open object in the schema; this is what the backend actually writes. */
export interface LastError {
  code?: string;
  message?: string | null;
  errors?: OrderProblem[];
  /** pending SD operation for outcome_unknown orders */
  op?: { name: 'submit' | 'admin_edit' | 'cancel' | 'status' | 'delete'; status?: number | null };
  /** reconcile gave up retrying; an admin resend restarts it */
  exhausted?: boolean;
}
type WithLastError<T> = Omit<T, 'last_error'> & { last_error?: LastError | null };

export type OrderSummary = WithLastError<S['OrderSummaryOut']>;

export type ShopOrderList = S['ShopOrderListOut'];

export type ImportPreview = S['ImportPreviewOut'];

export type AdminOrder = WithLastError<S['AdminOrderOut']>;

export type AdminOrderRow = WithLastError<S['AdminOrderRow']>;

export type AdminOrderList = S['AdminOrderListOut'];

export type Dashboard = S['DashboardOut'];

export type AdminRefs = S['RefsOut'];

export type AdminProduct = S['ProductOut'];

export type AdminProductWithWarnings = S['ProductBarcodeAddOut'];

export type OrderLine = S['OrderLineOut'];

export type OrderProblem = S['OrderProblem'];

export type OrderTotals = S['OrderTotals'];

export type CategorySummaryRow = S['CategorySummary'];

export type OrderEvent = S['OrderEventOut'];

/** Admin product search can return unpriced products, so `price` may be null here. */
export type CatalogItem = Omit<S['CatalogItem'], 'price'> & { price: number | null };

export type CatalogPage = S['CatalogOut'];

export type ImportMatched = S['ImportMatched'];

export type ImportAmbiguous = S['ImportAmbiguous'];

export type ImportUnmatched = S['ImportUnmatched'];

// ---- audit / reports / settings ----
export type AuditEntry = S['AuditOut'];
export type AuditList = S['AuditPage'];
export type AuditFacets = S['AuditFacets'];
export type SalesReport = S['SalesReport'];
export type ReportGroupBy = SalesReport['group_by'];

export const SYNC_ENTITIES = [
  'references',
  'products',
  'prices',
  'stock',
  'clients',
  'balances',
] as const;
export type SyncEntity = (typeof SYNC_ENTITIES)[number];

export const NOTIFY_EVENTS = [
  'new_order',
  'order_failed',
  'outcome_unknown',
  'reconcile_exhausted',
  'sync_error',
] as const;
export type NotifyEvent = (typeof NOTIFY_EVENTS)[number];

export interface IntervalSetting {
  default: number;
  override: number | null;
  effective: number;
  min: number;
  max: number;
}

export interface AppSettings {
  sync_intervals: Record<SyncEntity, IntervalSetting>;
  phone_max_clients: { default: number; override: number | null; effective: number };
  telegram: {
    enabled: boolean;
    token_configured: boolean;
    token_error: boolean;
    chat_ids: string[];
    events: NotifyEvent[];
    active: boolean;
    health?: {
      last_ok_at?: string | null;
      last_error?: string | null;
      last_error_at?: string | null;
      retry_until?: string | null;
    };
  };
  sync_enabled: boolean;
  sd: { host: string | null; credentials_configured: boolean; filial_id: number | null };
}

export interface AppSettingsUpdate {
  sync_intervals?: Partial<Record<SyncEntity, number | null>>;
  phone_max_clients?: number;
  reset_phone_max_clients?: boolean;
  telegram?: {
    enabled?: boolean;
    /** write-only; "" clears the stored token */
    bot_token?: string;
    chat_ids?: string[];
    events?: NotifyEvent[];
  };
}

// ---- waybill (накладная) ------------------------------------------------------------------------
export interface InvoiceLine {
  position: number;
  product_ref: string;
  name: string | null;
  thumb_url: string | null;
  unit: string | null;
  pack_quantity: number | null;
  blocks: number;
  quantity: number;
  price: number;
  discount: number;
  sum: number;
}

export interface Invoice {
  source: 'sd' | 'local';
  warning: 'sd_unavailable' | 'sd_not_found' | null;
  number: string;
  sd_id: string | null;
  code: string;
  sd_status: number | null;
  date: string | null;
  shipment_date: string | null;
  client: { name: string | null; phone: string | null; address: string | null };
  agent: string | null;
  warehouse: string | null;
  price_type: string | null;
  trade: string | null;
  comment: string | null;
  lines: InvoiceLine[];
  total_qty: number;
  total_sum: number;
  discount_sum: number;
  total_due: number;
}

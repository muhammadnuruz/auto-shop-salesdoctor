import type { TFunction } from 'i18next';
import { z } from 'zod';
import type {
  AdminLink,
  ClientScope,
  LinkCreateBody,
  LinkSettings,
  LinkUpdateBody,
} from '@/api/types';
import { isoToLocalInput, localInputToIso } from '@/lib/format';

export const SLUG_RE = /^[a-z0-9-]{6,64}$/;

export interface ChoiceForm {
  mode: 'fixed' | 'choice';
  ids: string[];
}
export interface AgentForm {
  mode: 'fixed' | 'choice' | 'client_assigned';
  ids: string[];
}

export interface LinkFormValues {
  name: string;
  slug: string;
  description: string;
  active: boolean;
  starts_at: string;
  ends_at: string;
  max_logins: string;
  client_scope: ClientScope;
  scope_values: string[];
  warehouses: ChoiceForm;
  price_types: ChoiceForm;
  agents: AgentForm;
  trade_directions: ChoiceForm;
  order_status: 1 | 2;
  min_order_sum: string;
  max_order_sum: string;
  allow_over_stock: boolean;
  product_filter: { mode: 'all' | 'categories' | 'brands' | 'products'; ids: string[] };
  branding: { title: string; logo_url: string; color: string; welcome_text: string };
}

export const defaultFormValues: LinkFormValues = {
  name: '',
  slug: '',
  description: '',
  active: true,
  starts_at: '',
  ends_at: '',
  max_logins: '',
  client_scope: 'all',
  scope_values: [],
  warehouses: { mode: 'choice', ids: [] },
  price_types: { mode: 'choice', ids: [] },
  agents: { mode: 'client_assigned', ids: [] },
  trade_directions: { mode: 'choice', ids: [] },
  order_status: 1,
  min_order_sum: '',
  max_order_sum: '',
  allow_over_stock: false,
  product_filter: { mode: 'all', ids: [] },
  branding: { title: '', logo_url: '', color: '', welcome_text: '' },
};

export function makeLinkSchema(t: TFunction, opts: { isEdit: boolean }) {
  const v = (k: string) => t(`links.validation.${k}`);
  const uniq = (ids: string[]) => new Set(ids).size === ids.length;

  const choice = z
    .object({ mode: z.enum(['fixed', 'choice']), ids: z.array(z.string()) })
    .superRefine((val, ctx) => {
      if (!uniq(val.ids)) ctx.addIssue({ code: 'custom', path: ['ids'], message: v('uniqueIds') });
      else if (val.mode === 'fixed' && val.ids.length !== 1)
        ctx.addIssue({ code: 'custom', path: ['ids'], message: v('fixedOne') });
      else if (val.mode === 'choice' && val.ids.length < 1)
        ctx.addIssue({ code: 'custom', path: ['ids'], message: v('choiceMin') });
    });

  const agents = z
    .object({ mode: z.enum(['fixed', 'choice', 'client_assigned']), ids: z.array(z.string()) })
    .superRefine((val, ctx) => {
      if (!uniq(val.ids)) ctx.addIssue({ code: 'custom', path: ['ids'], message: v('uniqueIds') });
      else if (val.mode === 'fixed' && val.ids.length !== 1)
        ctx.addIssue({ code: 'custom', path: ['ids'], message: v('fixedOne') });
      else if (val.mode === 'choice' && val.ids.length < 1)
        ctx.addIssue({ code: 'custom', path: ['ids'], message: v('choiceMin') });
    });

  const money = z
    .string()
    .trim()
    .refine((s) => s === '' || (Number.isFinite(Number(s)) && Number(s) >= 0), v('nonNegative'));

  return z
    .object({
      name: z.string().trim().min(1, v('nameRequired')).max(200),
      slug: opts.isEdit
        ? z.string()
        : z
            .string()
            .trim()
            .refine((s) => s === '' || SLUG_RE.test(s), v('slugPattern')),
      description: z.string().max(2000),
      active: z.boolean(),
      starts_at: z.string(),
      ends_at: z.string(),
      max_logins: z
        .string()
        .trim()
        .refine((s) => s === '' || (/^\d+$/.test(s) && Number(s) >= 1), v('positiveInt')),
      client_scope: z.enum(['all', 'selected', 'territory', 'category']),
      scope_values: z.array(z.string()),
      warehouses: choice,
      price_types: choice,
      agents,
      trade_directions: choice,
      order_status: z.union([z.literal(1), z.literal(2)]),
      min_order_sum: money,
      max_order_sum: money,
      allow_over_stock: z.boolean(),
      product_filter: z
        .object({
          mode: z.enum(['all', 'categories', 'brands', 'products']),
          ids: z.array(z.string()),
        })
        .superRefine((val, ctx) => {
          if (val.mode !== 'all' && val.ids.length === 0)
            ctx.addIssue({ code: 'custom', path: ['ids'], message: v('productIds') });
        }),
      branding: z.object({
        title: z.string().max(100),
        logo_url: z
          .string()
          .trim()
          .max(500)
          .refine((s) => s === '' || /^(https:\/\/|\/)\S*$/.test(s), v('logoUrl')),
        color: z
          .string()
          .trim()
          .refine((s) => s === '' || /^#[0-9a-fA-F]{6}$/.test(s), v('colorHex')),
        welcome_text: z.string().max(500),
      }),
    })
    .superRefine((val, ctx) => {
      if (
        (val.client_scope === 'territory' || val.client_scope === 'category') &&
        val.scope_values.length === 0
      )
        ctx.addIssue({ code: 'custom', path: ['scope_values'], message: v('scopeValuesRequired') });
      if (val.min_order_sum.trim() !== '' && val.max_order_sum.trim() !== '') {
        if (Number(val.min_order_sum) > Number(val.max_order_sum))
          ctx.addIssue({ code: 'custom', path: ['min_order_sum'], message: v('minGtMax') });
      }
      if (val.starts_at && val.ends_at && new Date(val.ends_at) <= new Date(val.starts_at))
        ctx.addIssue({ code: 'custom', path: ['ends_at'], message: v('endsBeforeStart') });
    });
}

export function linkToForm(link: AdminLink): LinkFormValues {
  const s = link.settings;
  return {
    name: link.name,
    slug: link.slug,
    description: link.description ?? '',
    active: link.active,
    starts_at: isoToLocalInput(link.starts_at),
    ends_at: isoToLocalInput(link.ends_at),
    max_logins: link.max_logins != null ? String(link.max_logins) : '',
    client_scope: link.client_scope,
    scope_values: link.scope_values,
    warehouses: { mode: s.warehouses.mode ?? 'choice', ids: s.warehouses.ids ?? [] },
    price_types: { mode: s.price_types.mode ?? 'choice', ids: s.price_types.ids ?? [] },
    agents: { mode: s.agents?.mode ?? 'client_assigned', ids: s.agents?.ids ?? [] },
    trade_directions: {
      mode: s.trade_directions.mode ?? 'choice',
      ids: s.trade_directions.ids ?? [],
    },
    order_status: s.order_status === 2 ? 2 : 1,
    min_order_sum: s.min_order_sum != null ? String(s.min_order_sum) : '',
    max_order_sum: s.max_order_sum != null ? String(s.max_order_sum) : '',
    allow_over_stock: s.allow_over_stock ?? false,
    product_filter: { mode: s.product_filter?.mode ?? 'all', ids: s.product_filter?.ids ?? [] },
    branding: {
      title: s.branding?.title ?? '',
      logo_url: s.branding?.logo_url ?? '',
      color: s.branding?.color ?? '',
      welcome_text: s.branding?.welcome_text ?? '',
    },
  };
}

const orNull = (s: string): string | null => (s.trim() === '' ? null : s.trim());
const numOrNull = (s: string): number | null => (s.trim() === '' ? null : Number(s));

export function formToSettings(f: LinkFormValues): LinkSettings {
  return {
    warehouses: f.warehouses,
    price_types: f.price_types,
    agents: f.agents,
    trade_directions: f.trade_directions,
    order_status: f.order_status,
    min_order_sum: numOrNull(f.min_order_sum),
    max_order_sum: numOrNull(f.max_order_sum),
    allow_over_stock: f.allow_over_stock,
    product_filter: f.product_filter.mode === 'all' ? { mode: 'all', ids: [] } : f.product_filter,
    branding: {
      title: orNull(f.branding.title),
      logo_url: orNull(f.branding.logo_url),
      color: orNull(f.branding.color),
      welcome_text: orNull(f.branding.welcome_text),
    },
  };
}

function common(f: LinkFormValues) {
  return {
    name: f.name.trim(),
    description: orNull(f.description),
    active: f.active,
    starts_at: localInputToIso(f.starts_at),
    ends_at: localInputToIso(f.ends_at),
    max_logins: f.max_logins.trim() === '' ? null : Number(f.max_logins),
    client_scope: f.client_scope,
    scope_values:
      f.client_scope === 'territory' || f.client_scope === 'category' ? f.scope_values : [],
    settings: formToSettings(f),
  };
}

export const formToCreate = (f: LinkFormValues): LinkCreateBody => ({
  ...common(f),
  slug: orNull(f.slug),
});
export const formToUpdate = (f: LinkFormValues): LinkUpdateBody => common(f);

/** Known top-level form fields, for mapping server validation paths. */
const FIELD_ROOTS = new Set([
  'name',
  'slug',
  'description',
  'active',
  'starts_at',
  'ends_at',
  'max_logins',
  'client_scope',
  'scope_values',
  'warehouses',
  'price_types',
  'agents',
  'trade_directions',
  'order_status',
  'min_order_sum',
  'max_order_sum',
  'allow_over_stock',
  'product_filter',
  'branding',
]);

const ID_GROUPS = new Set([
  'warehouses',
  'price_types',
  'agents',
  'trade_directions',
  'product_filter',
]);

/** Convert a backend issue (pydantic loc path or "field: message" string) to a form field path. */
export function serverPathToField(path: string, message: string): string | null {
  const p = path.startsWith('settings.') ? path.slice('settings.'.length) : path;
  const root = p.split('.')[0] ?? '';
  const target = FIELD_ROOTS.has(root) ? p : (/^([a-z_]+)[:\s]/.exec(message)?.[1] ?? '');
  if (!FIELD_ROOTS.has(target.split('.')[0] ?? '')) return null;
  // id lists live under `<group>.ids`; an error on the whole group is shown on its id input
  return ID_GROUPS.has(target) ? `${target}.ids` : target;
}

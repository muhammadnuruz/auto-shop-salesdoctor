import { zodResolver } from '@hookform/resolvers/zod';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import FormControlLabel from '@mui/material/FormControlLabel';
import MenuItem from '@mui/material/MenuItem';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import Switch from '@mui/material/Switch';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { useMemo, useState, type ReactNode } from 'react';
import { Controller, useForm, useWatch, type Control, type FieldPath } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/api/http';
import type { AdminLink } from '@/api/types';
import { ChipInput } from '@/components/ChipInput';
import { useRefs } from '@/features/admin/queries';
import { slugIsGuessable } from '@/lib/slug';
import {
  defaultFormValues,
  linkToForm,
  makeLinkSchema,
  serverPathToField,
  type LinkFormValues,
} from './schema';

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Paper variant="outlined" component="section" sx={{ p: { xs: 2, sm: 3 } }}>
      <Typography variant="h3" component="h2" sx={{ mb: 2 }}>
        {title}
      </Typography>
      {children}
    </Paper>
  );
}

const grid2 = {
  display: 'grid',
  gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' },
  gap: 2,
} as const;

type Group = 'warehouses' | 'price_types' | 'agents' | 'trade_directions';

function ChoiceSettingField({
  control,
  name,
  allowClientAssigned,
}: {
  control: Control<LinkFormValues>;
  name: Group;
  allowClientAssigned?: boolean;
}) {
  const { t } = useTranslation();
  const mode = useWatch({ control, name: `${name}.mode` });
  const refs = useRefs();
  const modes = allowClientAssigned
    ? (['fixed', 'choice', 'client_assigned'] as const)
    : (['fixed', 'choice'] as const);
  return (
    <Box
      sx={{
        display: 'grid',
        gridTemplateColumns: { xs: '1fr', sm: '220px 1fr' },
        gap: 2,
        alignItems: 'start',
      }}
    >
      <Controller
        control={control}
        name={`${name}.mode`}
        render={({ field }) => (
          <TextField
            select
            label={`${t(`links.fields.${name}`)} — ${t('links.fields.mode').toLowerCase()}`}
            {...field}
          >
            {modes.map((m) => (
              <MenuItem key={m} value={m}>
                {t(`links.modes.${m}`)}
              </MenuItem>
            ))}
          </TextField>
        )}
      />
      <Controller
        control={control}
        name={`${name}.ids`}
        render={({ field, fieldState }) => (
          <ChipInput
            label={`${t(`links.fields.${name}`)}: ${t('links.fields.ids')}`}
            options={refs.options(name)}
            loading={refs.isLoading}
            value={field.value}
            onChange={field.onChange}
            error={!!fieldState.error}
            helperText={
              fieldState.error?.message ??
              (mode === 'client_assigned' ? t('common.optional') : t('links.fields.idsHelp'))
            }
          />
        )}
      />
    </Box>
  );
}

interface Props {
  link?: AdminLink;
  submitLabel: string;
  onSubmit: (values: LinkFormValues) => Promise<unknown>;
}

export function LinkForm({ link, submitLabel, onSubmit }: Props) {
  const { t } = useTranslation();
  const isEdit = !!link;
  const schema = useMemo(() => makeLinkSchema(t, { isEdit }), [t, isEdit]);
  const form = useForm<LinkFormValues>({
    resolver: zodResolver(schema),
    defaultValues: link ? linkToForm(link) : defaultFormValues,
  });
  const { control, register, formState } = form;
  const { errors } = formState;
  const [formErrors, setFormErrors] = useState<string[]>([]);
  const scope = useWatch({ control, name: 'client_scope' });
  const productMode = useWatch({ control, name: 'product_filter.mode' });
  const refs = useRefs();
  const slugValue = useWatch({ control, name: 'slug' });

  const submit = form.handleSubmit(async (values) => {
    setFormErrors([]);
    try {
      await onSubmit(values);
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      const general: string[] = [];
      if (err.status === 409) {
        form.setError(
          'slug',
          { message: err.code ?? 'Slug already in use' },
          { shouldFocus: true },
        );
      } else if (err.status === 422) {
        for (const issue of err.issues) {
          const field = serverPathToField(issue.path, issue.message);
          if (field) form.setError(field as FieldPath<LinkFormValues>, { message: issue.message });
          else general.push(issue.message);
        }
        if (general.length === 0 && err.issues.length === 0)
          general.push(t('errors.validationFailed'));
      } else {
        throw err;
      }
      setFormErrors(general);
    }
  });

  const err = (path: FieldPath<LinkFormValues>) => {
    const parts = path.split('.');
    let cur: unknown = errors;
    for (const p of parts) cur = (cur as Record<string, unknown> | undefined)?.[p];
    return (cur as { message?: string } | undefined)?.message;
  };

  const text = (
    name: FieldPath<LinkFormValues>,
    label: string,
    extra?: {
      help?: string;
      multiline?: boolean;
      type?: string;
      inputMode?: 'numeric' | 'decimal';
      required?: boolean;
    },
  ) => (
    <TextField
      {...register(name)}
      label={label}
      type={extra?.type}
      multiline={extra?.multiline}
      minRows={extra?.multiline ? 2 : undefined}
      required={extra?.required}
      error={!!err(name)}
      helperText={err(name) ?? extra?.help}
      slotProps={{
        inputLabel: extra?.type === 'datetime-local' ? { shrink: true } : undefined,
        htmlInput: extra?.inputMode ? { inputMode: extra.inputMode } : undefined,
      }}
    />
  );

  return (
    <Box component="form" noValidate onSubmit={(e) => void submit(e)} aria-label={submitLabel}>
      <Stack spacing={2}>
        {(Object.keys(errors).length > 0 || formErrors.length > 0) && formState.isSubmitted && (
          <Alert severity="error" role="alert">
            {t('errors.validationFailed')}
            {formErrors.map((m) => (
              <div key={m}>{m}</div>
            ))}
          </Alert>
        )}

        <Section title={t('links.sections.main')}>
          <Stack spacing={2}>
            <Box sx={grid2}>
              {text('name', t('links.fields.name'), { required: true })}
              {isEdit ? (
                <TextField label={t('links.fields.slug')} value={link.slug} disabled />
              ) : (
                text('slug', t('links.fields.slug'), { help: t('links.fields.slugHelp') })
              )}
            </Box>
            {!isEdit && slugValue && slugIsGuessable(slugValue) && (
              <Alert severity="warning">{t('links.slugLowEntropy')}</Alert>
            )}
            {text('description', t('links.fields.description'), { multiline: true })}
            <Box sx={grid2}>
              {text('starts_at', t('links.fields.startsAt'), { type: 'datetime-local' })}
              {text('ends_at', t('links.fields.endsAt'), { type: 'datetime-local' })}
              {text('max_logins', t('links.fields.maxLogins'), {
                help: t('links.fields.maxLoginsHelp'),
                inputMode: 'numeric',
              })}
              <Controller
                control={control}
                name="active"
                render={({ field }) => (
                  <FormControlLabel
                    control={
                      <Switch checked={field.value} onChange={(_, v) => field.onChange(v)} />
                    }
                    label={t('links.fields.active')}
                  />
                )}
              />
            </Box>
          </Stack>
        </Section>

        <Section title={t('links.sections.access')}>
          <Stack spacing={2}>
            <Controller
              control={control}
              name="client_scope"
              render={({ field }) => (
                <TextField select label={t('links.fields.clientScope')} {...field}>
                  {(['all', 'selected', 'territory', 'category'] as const).map((s) => (
                    <MenuItem key={s} value={s}>
                      {t(`links.scopes.${s}`)}
                    </MenuItem>
                  ))}
                </TextField>
              )}
            />
            {(scope === 'territory' || scope === 'category') && (
              <Controller
                control={control}
                name="scope_values"
                render={({ field, fieldState }) => (
                  <ChipInput
                    label={t('links.fields.scopeValues')}
                    options={refs.options(
                      scope === 'category' ? 'client_categories' : 'territories',
                    )}
                    loading={refs.isLoading}
                    value={field.value}
                    onChange={field.onChange}
                    error={!!fieldState.error}
                    helperText={fieldState.error?.message ?? t('links.fields.scopeValuesHelp')}
                  />
                )}
              />
            )}
            {scope === 'selected' && <Alert severity="info">{t('links.clientsScopeHint')}</Alert>}
          </Stack>
        </Section>

        <Section title={t('links.sections.order')}>
          <Stack spacing={2.5}>
            <ChoiceSettingField control={control} name="warehouses" />
            <ChoiceSettingField control={control} name="price_types" />
            <ChoiceSettingField control={control} name="agents" allowClientAssigned />
            <ChoiceSettingField control={control} name="trade_directions" />
            <Controller
              control={control}
              name="order_status"
              render={({ field }) => (
                <TextField
                  select
                  label={t('links.fields.orderStatus')}
                  value={field.value}
                  onChange={(e) => field.onChange(Number(e.target.value))}
                >
                  {[1, 2].map((s) => (
                    <MenuItem key={s} value={s}>
                      {t(`links.orderStatuses.${s}`)}
                    </MenuItem>
                  ))}
                </TextField>
              )}
            />
          </Stack>
        </Section>

        <Section title={t('links.sections.limits')}>
          <Box sx={grid2}>
            {text('min_order_sum', t('links.fields.minOrderSum'), { inputMode: 'decimal' })}
            {text('max_order_sum', t('links.fields.maxOrderSum'), { inputMode: 'decimal' })}
            <Controller
              control={control}
              name="allow_over_stock"
              render={({ field }) => (
                <FormControlLabel
                  control={<Switch checked={field.value} onChange={(_, v) => field.onChange(v)} />}
                  label={t('links.fields.allowOverStock')}
                />
              )}
            />
          </Box>
        </Section>

        <Section title={t('links.sections.products')}>
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: '1fr', sm: '220px 1fr' },
              gap: 2,
              alignItems: 'start',
            }}
          >
            <Controller
              control={control}
              name="product_filter.mode"
              render={({ field }) => (
                <TextField select label={t('links.fields.productFilter')} {...field}>
                  {(['all', 'categories', 'brands', 'products'] as const).map((m) => (
                    <MenuItem key={m} value={m}>
                      {t(`links.productModes.${m}`)}
                    </MenuItem>
                  ))}
                </TextField>
              )}
            />
            {productMode !== 'all' && (
              <Controller
                control={control}
                name="product_filter.ids"
                render={({ field, fieldState }) => (
                  <ChipInput
                    label={t('links.fields.ids')}
                    options={
                      productMode === 'brands'
                        ? refs.options('brands')
                        : productMode === 'categories'
                          ? refs.options('categories')
                          : undefined
                    }
                    loading={refs.isLoading}
                    value={field.value}
                    onChange={field.onChange}
                    error={!!fieldState.error}
                    helperText={fieldState.error?.message ?? t('links.fields.idsHelp')}
                  />
                )}
              />
            )}
          </Box>
        </Section>

        <Section title={t('links.sections.branding')}>
          <Stack spacing={2}>
            <Box sx={grid2}>
              {text('branding.title', t('links.fields.brandTitle'))}
              {text('branding.color', t('links.fields.brandColor'), { help: '#00796B' })}
              {text('branding.logo_url', t('links.fields.brandLogo'), {
                help: t('links.fields.brandLogoHelp'),
              })}
            </Box>
            {text('branding.welcome_text', t('links.fields.brandWelcome'), { multiline: true })}
          </Stack>
        </Section>

        <Stack direction="row" justifyContent="flex-end">
          <Button type="submit" variant="contained" size="large" disabled={formState.isSubmitting}>
            {submitLabel}
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
}

import { zodResolver } from '@hookform/resolvers/zod';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import CircularProgress from '@mui/material/CircularProgress';
import List from '@mui/material/List';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemText from '@mui/material/ListItemText';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { z } from 'zod';
import { ApiError } from '@/api/http';
import { shopApi } from '@/api/shop';
import type { ChoiceOption } from '@/api/types';
import { PhoneField } from '@/components/PhoneField';
import { describeError } from '@/lib/errors';
import { toE164 } from '@/lib/phone';
import { useCountdown } from '@/hooks/useCountdown';
import { refreshShopMe } from './queries';

export function LoginCard({ slug, welcome }: { slug: string; welcome?: string | null }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [choices, setChoices] = useState<ChoiceOption[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { remaining, start } = useCountdown();

  const schema = useMemo(
    () => z.object({ phone: z.string().regex(/^\d{9}$/, t('shop.phoneInvalid')) }),
    [t],
  );
  const form = useForm<{ phone: string }>({
    resolver: zodResolver(schema),
    defaultValues: { phone: '' },
    mode: 'onSubmit',
  });

  useEffect(() => {
    // re-validate messages when the language changes
    if (form.formState.isSubmitted) void form.trigger();
  }, [t, form]);

  const finish = async () => {
    await refreshShopMe(qc);
    navigate(`/s/${slug}/home`, { replace: true });
  };

  const onError = (err: unknown, fromSelect: boolean) => {
    if (err instanceof ApiError) {
      if (err.status === 429) {
        const s = err.retryAfter ?? 30;
        start(s);
        setError(
          err.retryAfter != null ? t('errors.tooMany', { seconds: s }) : t('errors.tooManyNoTime'),
        );
        return;
      }
      if (err.status === 401) {
        if (fromSelect) {
          setChoices(null);
          setError(t('shop.chooseExpired'));
        } else setError(t('shop.denied'));
        return;
      }
      if (err.status === 403) return setError(t('shop.inactiveOnLogin'));
      if (err.status === 422) return setError(t('shop.badPhone'));
    }
    setError(describeError(err, t));
  };

  const login = useMutation({
    mutationFn: (digits: string) => shopApi.login(slug, toE164(digits)),
    meta: { silent: true },
    onMutate: () => setError(null),
    onSuccess: async (res) => {
      if (res.choose && res.choose.length > 0) setChoices(res.choose);
      else await finish();
    },
    onError: (e) => onError(e, false),
  });

  const select = useMutation({
    mutationFn: (ref: string) => shopApi.select(slug, ref),
    meta: { silent: true },
    onMutate: () => setError(null),
    onSuccess: finish,
    onError: (e) => onError(e, true),
  });

  const busy = login.isPending || select.isPending;

  if (choices) {
    return (
      <Stack spacing={2}>
        <Typography variant="h2">{t('shop.chooseTitle')}</Typography>
        <Typography color="text.secondary">{t('shop.chooseText')}</Typography>
        {error && (
          <Alert severity="error" role="alert">
            {error}
          </Alert>
        )}
        <List
          aria-label={t('shop.chooseTitle')}
          sx={{ border: 1, borderColor: 'divider', borderRadius: 1, p: 0 }}
        >
          {choices.map((c) => (
            <ListItemButton
              key={c.client_ref}
              disabled={busy || remaining > 0}
              onClick={() => select.mutate(c.client_ref)}
              divider
              sx={{ minHeight: 56 }}
            >
              <ListItemText primary={c.name ?? '—'} secondary={c.address ?? t('shop.noAddress')} />
            </ListItemButton>
          ))}
        </List>
        <Button
          onClick={() => {
            setChoices(null);
            setError(null);
          }}
          disabled={busy}
        >
          {t('shop.changeNumber')}
        </Button>
      </Stack>
    );
  }

  return (
    <Box component="form" noValidate onSubmit={form.handleSubmit((v) => login.mutate(v.phone))}>
      <Stack spacing={2}>
        <Typography variant="h2">{t('shop.linkTitle')}</Typography>
        <Typography color="text.secondary">{welcome || t('shop.welcomeDefault')}</Typography>
        {error && (
          <Alert severity={remaining > 0 ? 'warning' : 'error'} role="alert">
            {error}
          </Alert>
        )}
        <Controller
          control={form.control}
          name="phone"
          render={({ field, fieldState }) => (
            <PhoneField
              label={t('shop.phone')}
              value={field.value}
              onDigitsChange={field.onChange}
              onBlur={field.onBlur}
              inputRef={field.ref}
              error={!!fieldState.error}
              helperText={fieldState.error?.message ?? t('shop.phoneHelp')}
              autoFocus
              required
              slotProps={{ htmlInput: { 'aria-describedby': undefined } }}
              sx={{ '& input': { fontSize: '1.15rem' } }}
            />
          )}
        />
        <Button
          type="submit"
          variant="contained"
          size="large"
          disabled={busy || remaining > 0}
          startIcon={busy ? <CircularProgress size={18} color="inherit" /> : undefined}
        >
          {remaining > 0
            ? t('shop.retryIn', { seconds: remaining })
            : busy
              ? t('shop.loggingIn')
              : t('shop.login')}
        </Button>
      </Stack>
    </Box>
  );
}

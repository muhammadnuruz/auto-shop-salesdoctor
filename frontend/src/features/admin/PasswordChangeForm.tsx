import { zodResolver } from '@hookform/resolvers/zod';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Stack from '@mui/material/Stack';
import { useMutation } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';
import { authApi } from '@/api/admin';
import { ApiError } from '@/api/http';
import { PasswordField } from '@/components/PasswordField';
import { describeError } from '@/lib/errors';

interface Values {
  current_password: string;
  new_password: string;
  confirm: string;
}

export function PasswordChangeForm({ onChanged }: { onChanged: () => void }) {
  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);
  const schema = useMemo(
    () =>
      z
        .object({
          current_password: z.string().min(1, t('common.required')),
          new_password: z.string().min(12, t('adminAuth.passwordMin')).max(256),
          confirm: z.string(),
        })
        .refine((v) => v.new_password === v.confirm, {
          path: ['confirm'],
          message: t('adminAuth.passwordMismatch'),
        })
        .refine((v) => v.new_password !== v.current_password, {
          path: ['new_password'],
          message: t('adminAuth.passwordSame'),
        }),
    [t],
  );
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { current_password: '', new_password: '', confirm: '' },
  });
  const { errors } = form.formState;

  const change = useMutation({
    mutationFn: (v: Values) =>
      authApi.changePassword({
        current_password: v.current_password,
        new_password: v.new_password,
      }),
    meta: { silent: true },
    onMutate: () => setError(null),
    onSuccess: () => {
      form.reset();
      onChanged();
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 400) setError(t('adminAuth.wrongCurrent'));
      else setError(describeError(err, t));
    },
  });

  return (
    <form noValidate onSubmit={form.handleSubmit((v) => change.mutate(v))}>
      <Stack spacing={2}>
        {error && (
          <Alert severity="error" role="alert">
            {error}
          </Alert>
        )}
        <PasswordField
          {...form.register('current_password')}
          label={t('adminAuth.currentPassword')}
          autoComplete="current-password"
          error={!!errors.current_password}
          helperText={errors.current_password?.message}
        />
        <PasswordField
          {...form.register('new_password')}
          label={t('adminAuth.newPassword')}
          autoComplete="new-password"
          error={!!errors.new_password}
          helperText={errors.new_password?.message ?? t('adminAuth.passwordMin')}
        />
        <PasswordField
          {...form.register('confirm')}
          label={t('adminAuth.confirmPassword')}
          autoComplete="new-password"
          error={!!errors.confirm}
          helperText={errors.confirm?.message}
        />
        <Button type="submit" variant="contained" disabled={change.isPending}>
          {t('common.save')}
        </Button>
      </Stack>
    </form>
  );
}

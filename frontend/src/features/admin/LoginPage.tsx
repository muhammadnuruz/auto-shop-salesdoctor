import { zodResolver } from '@hookform/resolvers/zod';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import CircularProgress from '@mui/material/CircularProgress';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { z } from 'zod';
import { authApi } from '@/api/admin';
import { ApiError } from '@/api/http';
import { PasswordField } from '@/components/PasswordField';
import { describeError } from '@/lib/errors';
import { AuthShell } from './AuthShell';
import { setAdminMe, useAdminMe } from './queries';

interface Creds {
  username: string;
  password: string;
}

export default function AdminLoginPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const qc = useQueryClient();
  const me = useAdminMe();
  const [creds, setCreds] = useState<Creds | null>(null); // kept in memory only, for the TOTP step
  const [totpStep, setTotpStep] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const schema = useMemo(
    () =>
      z.object({
        username: z.string().trim().min(1, t('common.required')),
        password: z.string().min(1, t('common.required')),
      }),
    [t],
  );
  const totpSchema = useMemo(
    () => z.object({ totp: z.string().regex(/^\d{6}$/, t('adminAuth.totpInvalid')) }),
    [t],
  );
  const credForm = useForm<Creds>({
    resolver: zodResolver(schema),
    defaultValues: { username: '', password: '' },
  });
  const totpForm = useForm<{ totp: string }>({
    resolver: zodResolver(totpSchema),
    defaultValues: { totp: '' },
  });

  const from = (location.state as { from?: string } | null)?.from;

  const login = useMutation({
    mutationFn: (v: Creds & { totp?: string }) => authApi.login(v),
    meta: { silent: true },
    onMutate: () => setError(null),
    onSuccess: (res) => {
      setAdminMe(qc, res.user, res.csrf_token);
      setCreds(null);
      navigate(res.must_change_password ? '/admin/change-password' : (from ?? '/admin'), {
        replace: true,
      });
    },
    onError: (err, vars) => {
      if (err instanceof ApiError && err.status === 401) {
        if (err.code === 'totp_required') {
          setTotpStep(true);
          return;
        }
        setError(vars.totp ? t('adminAuth.badTotp') : t('adminAuth.badCredentials'));
        if (vars.totp) totpForm.reset();
        return;
      }
      setError(describeError(err, t));
    },
  });

  if (me.data && !totpStep) {
    return (
      <Navigate
        to={me.data.user.must_change_password ? '/admin/change-password' : (from ?? '/admin')}
        replace
      />
    );
  }

  if (totpStep && creds) {
    return (
      <AuthShell title={t('adminAuth.totpTitle')}>
        <Typography color="text.secondary">{t('adminAuth.totpText')}</Typography>
        {error && (
          <Alert severity="error" role="alert">
            {error}
          </Alert>
        )}
        <form
          noValidate
          onSubmit={totpForm.handleSubmit((v) => login.mutate({ ...creds, totp: v.totp }))}
        >
          <Stack spacing={2}>
            <TextField
              {...totpForm.register('totp')}
              label={t('adminAuth.totpCode')}
              autoFocus
              error={!!totpForm.formState.errors.totp}
              helperText={totpForm.formState.errors.totp?.message}
              slotProps={{
                htmlInput: { inputMode: 'numeric', autoComplete: 'one-time-code', maxLength: 6 },
              }}
            />
            <Button
              type="submit"
              variant="contained"
              size="large"
              disabled={login.isPending}
              startIcon={
                login.isPending ? <CircularProgress size={18} color="inherit" /> : undefined
              }
            >
              {t('adminAuth.verify')}
            </Button>
            <Button
              onClick={() => {
                setTotpStep(false);
                setCreds(null);
                setError(null);
              }}
            >
              {t('adminAuth.backToLogin')}
            </Button>
          </Stack>
        </form>
      </AuthShell>
    );
  }

  return (
    <AuthShell title={t('adminAuth.title')}>
      {error && (
        <Alert severity="error" role="alert">
          {error}
        </Alert>
      )}
      <form
        noValidate
        onSubmit={credForm.handleSubmit((v) => {
          setCreds(v);
          login.mutate(v);
        })}
      >
        <Stack spacing={2}>
          <TextField
            {...credForm.register('username')}
            label={t('adminAuth.username')}
            autoComplete="username"
            autoFocus
            error={!!credForm.formState.errors.username}
            helperText={credForm.formState.errors.username?.message}
            slotProps={{ htmlInput: { autoCapitalize: 'off', spellCheck: false } }}
          />
          <PasswordField
            {...credForm.register('password')}
            label={t('adminAuth.password')}
            autoComplete="current-password"
            error={!!credForm.formState.errors.password}
            helperText={credForm.formState.errors.password?.message}
          />
          <Button
            type="submit"
            variant="contained"
            size="large"
            disabled={login.isPending}
            startIcon={login.isPending ? <CircularProgress size={18} color="inherit" /> : undefined}
          >
            {t('adminAuth.signIn')}
          </Button>
        </Stack>
      </form>
    </AuthShell>
  );
}

import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import QRCode from 'qrcode';
import { useSnackbar } from 'notistack';
import { useEffect, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { authApi } from '@/api/admin';
import { ApiError } from '@/api/http';
import type { TotpSetup } from '@/api/types';
import { CopyButton } from '@/components/CopyButton';
import { PageHeader } from '@/components/PageHeader';
import { PasswordField } from '@/components/PasswordField';
import { describeError } from '@/lib/errors';
import { useCurrentAdmin } from '../AdminContext';
import { PasswordChangeForm } from '../PasswordChangeForm';
import { refreshAdminMe } from '../queries';

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Paper variant="outlined" component="section" sx={{ p: { xs: 2, sm: 3 } }}>
      <Typography variant="h3" component="h2" sx={{ mb: 2 }}>
        {title}
      </Typography>
      {children}
    </Paper>
  );
}

function TotpQr({ uri }: { uri: string }) {
  const { t } = useTranslation();
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let off = false;
    QRCode.toDataURL(uri, { width: 240, margin: 2 })
      .then((d) => !off && setSrc(d))
      .catch(() => undefined);
    return () => {
      off = true;
    };
  }, [uri]);
  return src ? (
    <Box
      component="img"
      src={src}
      alt={t('profile.qrAlt')}
      sx={{ width: 200, height: 200, bgcolor: '#fff', borderRadius: 1 }}
    />
  ) : null;
}

function TotpSection() {
  const { t } = useTranslation();
  const user = useCurrentAdmin();
  const qc = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);

  const onErr = (err: unknown) =>
    setError(
      err instanceof ApiError && err.status === 400
        ? t('profile.totpBadCode')
        : describeError(err, t),
    );
  const reset = () => {
    setPassword('');
    setCode('');
    setError(null);
    setSetup(null);
  };

  const start = useMutation({
    mutationFn: () => authApi.totpSetup(password),
    meta: { silent: true },
    onMutate: () => setError(null),
    onSuccess: (res) => setSetup(res),
    onError: (e) =>
      e instanceof ApiError && e.status === 400 ? setError(t('adminAuth.wrongCurrent')) : onErr(e),
  });
  const enable = useMutation({
    mutationFn: () => authApi.totpEnable({ code, password }),
    meta: { silent: true },
    onMutate: () => setError(null),
    onSuccess: async () => {
      enqueueSnackbar(t('profile.totpEnableDone'), { variant: 'success' });
      reset();
      await refreshAdminMe(qc);
    },
    onError: onErr,
  });
  const disable = useMutation({
    mutationFn: () => authApi.totpDisable({ code, password }),
    meta: { silent: true },
    onMutate: () => setError(null),
    onSuccess: async () => {
      enqueueSnackbar(t('profile.totpDisableDone'), { variant: 'success' });
      reset();
      await refreshAdminMe(qc);
    },
    onError: onErr,
  });

  const submit = (fn: () => void) => (e: FormEvent) => {
    e.preventDefault();
    fn();
  };
  const codeField = (
    <TextField
      label={t('profile.totpCodeLabel')}
      value={code}
      onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
      slotProps={{ htmlInput: { inputMode: 'numeric', autoComplete: 'one-time-code' } }}
      required
    />
  );

  return (
    <Card title={t('profile.twoFactor')}>
      <Stack spacing={2}>
        <Box>
          <Chip
            color={user.totp_enabled ? 'success' : 'default'}
            label={user.totp_enabled ? t('profile.totpOn') : t('profile.totpOff')}
          />
        </Box>
        {error && (
          <Alert severity="error" role="alert">
            {error}
          </Alert>
        )}
        {user.totp_enabled ? (
          <form onSubmit={submit(() => disable.mutate())}>
            <Stack spacing={2}>
              <Typography color="text.secondary">{t('profile.totpDisableText')}</Typography>
              <PasswordField
                label={t('profile.totpPasswordLabel')}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
              {codeField}
              <Button
                type="submit"
                color="error"
                variant="outlined"
                disabled={disable.isPending || !password || code.length !== 6}
              >
                {t('profile.totpDisable')}
              </Button>
            </Stack>
          </form>
        ) : setup ? (
          <form onSubmit={submit(() => enable.mutate())}>
            <Stack spacing={2} alignItems="flex-start">
              <Typography color="text.secondary">{t('profile.totpScan')}</Typography>
              <TotpQr uri={setup.otpauth_uri} />
              <Stack direction="row" alignItems="center" spacing={0.5} sx={{ width: '100%' }}>
                <TextField
                  label={t('profile.totpSecret')}
                  value={setup.secret}
                  slotProps={{ input: { readOnly: true, sx: { fontFamily: 'monospace' } } }}
                />
                <CopyButton value={setup.secret} />
              </Stack>
              {codeField}
              <Stack direction="row" spacing={1}>
                <Button
                  type="submit"
                  variant="contained"
                  disabled={enable.isPending || code.length !== 6}
                >
                  {t('profile.totpEnableStart')}
                </Button>
                <Button onClick={reset}>{t('common.cancel')}</Button>
              </Stack>
            </Stack>
          </form>
        ) : (
          <form onSubmit={submit(() => start.mutate())}>
            <Stack spacing={2}>
              <PasswordField
                label={t('profile.totpPasswordLabel')}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
              <Button type="submit" variant="contained" disabled={start.isPending || !password}>
                {t('profile.totpEnableStart')}
              </Button>
            </Stack>
          </form>
        )}
      </Stack>
    </Card>
  );
}

export default function ProfilePage() {
  const { t } = useTranslation();
  const user = useCurrentAdmin();
  const { enqueueSnackbar } = useSnackbar();
  return (
    <>
      <PageHeader title={t('profile.title')} />
      <Stack spacing={2} sx={{ maxWidth: 720 }}>
        <Card title={t('profile.account')}>
          <Stack spacing={1}>
            <Typography>
              <b>{user.full_name || user.username}</b> ({user.username})
            </Typography>
            <Typography color="text.secondary">
              {t('profile.role')}: {t(`users.roles.${user.role}`)}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {t('profile.permissions')}:
            </Typography>
            <Stack direction="row" flexWrap="wrap" useFlexGap spacing={0.5}>
              {user.permissions.map((p) => (
                <Chip key={p} size="small" variant="outlined" label={p} />
              ))}
            </Stack>
          </Stack>
        </Card>
        <Card title={t('profile.changePassword')}>
          <PasswordChangeForm
            onChanged={() =>
              enqueueSnackbar(t('adminAuth.passwordChanged'), { variant: 'success' })
            }
          />
        </Card>
        <TotpSection />
      </Stack>
    </>
  );
}

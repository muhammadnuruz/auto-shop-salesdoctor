import SaveIcon from '@mui/icons-material/Save';
import SendIcon from '@mui/icons-material/Send';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Checkbox from '@mui/material/Checkbox';
import Chip from '@mui/material/Chip';
import FormControlLabel from '@mui/material/FormControlLabel';
import FormGroup from '@mui/material/FormGroup';
import Paper from '@mui/material/Paper';
import Skeleton from '@mui/material/Skeleton';
import Stack from '@mui/material/Stack';
import Switch from '@mui/material/Switch';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSnackbar } from 'notistack';
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { settingsApi } from '@/api/admin';
import { qk } from '@/api/keys';
import {
  NOTIFY_EVENTS,
  SYNC_ENTITIES,
  type AppSettings,
  type AppSettingsUpdate,
  type NotifyEvent,
  type SyncEntity,
} from '@/api/types';
import { ChipInput } from '@/components/ChipInput';
import { PageHeader } from '@/components/PageHeader';
import { PasswordField } from '@/components/PasswordField';
import { ErrorState } from '@/components/States';
import { formatDateTime, formatDuration } from '@/lib/format';

export const CHAT_ID_RE = /^(-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$/;
export const BOT_TOKEN_RE = /^\d{5,15}:[A-Za-z0-9_-]{30,64}$/;

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="h2" sx={{ fontSize: '1.15rem', mb: 1.5 }}>
        {title}
      </Typography>
      {children}
    </Paper>
  );
}

function useSave() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  return useMutation({
    mutationFn: (body: AppSettingsUpdate) => settingsApi.update(body),
    onSuccess: (data) => {
      qc.setQueryData(qk.admin.settings(), data);
      enqueueSnackbar(t('settings.saved'), { variant: 'success' });
    },
  });
}

function SdCard({ s }: { s: AppSettings }) {
  const { t } = useTranslation();
  return (
    <Section title={t('settings.sdTitle')}>
      <Stack spacing={1}>
        <Typography>
          {t('settings.sdHost')}: <b>{s.sd.host ?? '—'}</b>
        </Typography>
        <Alert severity={s.sd.credentials_configured ? 'success' : 'warning'}>
          {s.sd.credentials_configured
            ? t('settings.sdCredentials')
            : t('settings.sdNoCredentials')}
        </Alert>
      </Stack>
    </Section>
  );
}

function SyncCard({ s }: { s: AppSettings }) {
  const { t } = useTranslation();
  const save = useSave();
  const initial = Object.fromEntries(
    SYNC_ENTITIES.map((e) => [e, s.sync_intervals[e].override?.toString() ?? '']),
  ) as Record<SyncEntity, string>;
  const [values, setValues] = useState(initial);

  const errorOf = (e: SyncEntity): string | null => {
    const v = values[e].trim();
    if (v === '') return null;
    const n = Number(v);
    const { min, max } = s.sync_intervals[e];
    return Number.isInteger(n) && n >= min && n <= max
      ? null
      : t('settings.invalidInterval', { min, max });
  };
  const changed = SYNC_ENTITIES.filter((e) => values[e].trim() !== initial[e]);
  const invalid = SYNC_ENTITIES.some((e) => errorOf(e) != null);

  const submit = () => {
    const body: Partial<Record<SyncEntity, number | null>> = {};
    for (const e of changed) body[e] = values[e].trim() === '' ? null : Number(values[e]);
    save.mutate({ sync_intervals: body });
  };

  return (
    <Section title={t('settings.syncTitle')}>
      {!s.sync_enabled && (
        <Alert severity="warning" sx={{ mb: 1 }}>
          {t('settings.syncDisabled')}
        </Alert>
      )}
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        {t('settings.syncHint')}
      </Typography>
      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small" aria-label={t('settings.syncTitle')}>
          <TableHead>
            <TableRow>
              <TableCell>{t('sync.entity')}</TableCell>
              <TableCell>{t('settings.colDefault')}</TableCell>
              <TableCell>{t('settings.colOverride')}</TableCell>
              <TableCell>{t('settings.colEffective')}</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {SYNC_ENTITIES.map((e) => {
              const it = s.sync_intervals[e];
              const err = errorOf(e);
              return (
                <TableRow key={e}>
                  <TableCell>{t(`sync.entities.${e}`)}</TableCell>
                  <TableCell>{formatDuration(it.default * 1000)}</TableCell>
                  <TableCell sx={{ minWidth: 180 }}>
                    <TextField
                      size="small"
                      type="number"
                      value={values[e]}
                      onChange={(ev) => setValues((v) => ({ ...v, [e]: ev.target.value }))}
                      error={err != null}
                      helperText={err ?? t('settings.range', { min: it.min, max: it.max })}
                      inputProps={{
                        min: it.min,
                        max: it.max,
                        'aria-label': t(`sync.entities.${e}`),
                      }}
                    />
                  </TableCell>
                  <TableCell>{formatDuration(it.effective * 1000)}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Box>
      <Button
        sx={{ mt: 1.5 }}
        variant="contained"
        startIcon={<SaveIcon />}
        disabled={changed.length === 0 || invalid || save.isPending}
        onClick={submit}
      >
        {t('common.save')}
      </Button>
    </Section>
  );
}

function PhoneCard({ s }: { s: AppSettings }) {
  const { t } = useTranslation();
  const save = useSave();
  const [value, setValue] = useState(String(s.phone_max_clients.effective));
  const n = Number(value);
  const valid = Number.isInteger(n) && n >= 1 && n <= 50;
  return (
    <Section title={t('settings.phoneTitle')}>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
        {t('settings.phoneHint')}
      </Typography>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ sm: 'center' }}>
        <TextField
          size="small"
          type="number"
          label={t('settings.phoneMax')}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          error={!valid}
          helperText={t('settings.phoneDefault', { n: s.phone_max_clients.default })}
          inputProps={{ min: 1, max: 50 }}
        />
        <Button
          variant="contained"
          startIcon={<SaveIcon />}
          disabled={!valid || n === s.phone_max_clients.effective || save.isPending}
          onClick={() => save.mutate({ phone_max_clients: n })}
        >
          {t('common.save')}
        </Button>
        {s.phone_max_clients.override != null && (
          <Button
            disabled={save.isPending}
            onClick={() => {
              setValue(String(s.phone_max_clients.default));
              save.mutate({ reset_phone_max_clients: true });
            }}
          >
            {t('common.reset')}
          </Button>
        )}
      </Stack>
    </Section>
  );
}

function TelegramCard({ s }: { s: AppSettings }) {
  const { t } = useTranslation();
  const save = useSave();
  const { enqueueSnackbar } = useSnackbar();
  const tg = s.telegram;
  const [enabled, setEnabled] = useState(tg.enabled);
  const [token, setToken] = useState('');
  const [chats, setChats] = useState<string[]>(tg.chat_ids);
  const [events, setEvents] = useState<NotifyEvent[]>(tg.events);

  const badChats = chats.filter((c) => !CHAT_ID_RE.test(c));
  const tokenInvalid = token !== '' && !BOT_TOKEN_RE.test(token.trim());

  const test = useMutation({
    mutationFn: settingsApi.telegramTest,
    onSuccess: (res) => {
      if (res.ok) enqueueSnackbar(t('settings.testOk'), { variant: 'success' });
      else {
        const details = Object.entries(res.results)
          .filter(([, v]) => v !== 'ok')
          .map(([k, v]) => `${k}: ${v}`)
          .join(', ');
        enqueueSnackbar(t('settings.testFailed', { details }), { variant: 'warning' });
      }
    },
  });

  const submit = () => {
    const body: NonNullable<AppSettingsUpdate['telegram']> = {
      enabled,
      chat_ids: chats,
      events,
    };
    if (token.trim() !== '') body.bot_token = token.trim();
    save.mutate({ telegram: body }, { onSuccess: () => setToken('') });
  };

  return (
    <Section title={t('settings.telegramTitle')}>
      <Stack spacing={1.5}>
        <Box>
          <Chip
            size="small"
            color={tg.active ? 'success' : 'default'}
            label={tg.active ? t('settings.active') : t('settings.inactive')}
          />
        </Box>
        {tg.token_error && <Alert severity="error">{t('settings.botTokenError')}</Alert>}
        {tg.health?.last_error &&
          (!tg.health.last_ok_at ||
            (tg.health.last_error_at ?? '') > (tg.health.last_ok_at ?? '')) && (
            <Alert severity="warning">
              {t('settings.lastError', {
                error: tg.health.last_error,
                at: formatDateTime(tg.health.last_error_at),
              })}
            </Alert>
          )}
        {tg.health?.last_ok_at && (
          <Typography variant="body2" color="text.secondary">
            {t('settings.lastOk', { at: formatDateTime(tg.health.last_ok_at) })}
          </Typography>
        )}
        <FormControlLabel
          control={<Switch checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />}
          label={t('settings.telegramEnabled')}
        />
        <Stack
          direction={{ xs: 'column', sm: 'row' }}
          spacing={1}
          alignItems={{ sm: 'flex-start' }}
        >
          <PasswordField
            size="small"
            fullWidth
            autoComplete="off"
            label={t('settings.botToken')}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            error={tokenInvalid}
            helperText={
              tokenInvalid
                ? t('settings.invalidToken')
                : tg.token_configured
                  ? t('settings.botTokenSet')
                  : t('settings.botTokenHelp')
            }
          />
          {tg.token_configured && (
            <Button
              color="error"
              disabled={save.isPending}
              onClick={() => save.mutate({ telegram: { bot_token: '' } })}
              sx={{ whiteSpace: 'nowrap' }}
            >
              {t('settings.clearToken')}
            </Button>
          )}
        </Stack>
        <ChipInput
          label={t('settings.chatIds')}
          value={chats}
          onChange={setChats}
          error={badChats.length > 0}
          helperText={
            badChats.length > 0
              ? `${t('settings.invalidChat')}: ${badChats.join(', ')}`
              : t('settings.chatIdsHelp')
          }
        />
        <Box>
          <Typography variant="subtitle2">{t('settings.events')}</Typography>
          <FormGroup>
            {NOTIFY_EVENTS.map((e) => (
              <FormControlLabel
                key={e}
                control={
                  <Checkbox
                    checked={events.includes(e)}
                    onChange={(ev) =>
                      setEvents((cur) =>
                        ev.target.checked ? [...cur, e] : cur.filter((x) => x !== e),
                      )
                    }
                  />
                }
                label={t(`settings.eventNames.${e}`)}
              />
            ))}
          </FormGroup>
        </Box>
        <Stack direction="row" spacing={1}>
          <Button
            variant="contained"
            startIcon={<SaveIcon />}
            disabled={save.isPending || tokenInvalid || badChats.length > 0}
            onClick={submit}
          >
            {t('common.save')}
          </Button>
          <Button
            variant="outlined"
            startIcon={<SendIcon />}
            disabled={test.isPending || !tg.token_configured || tg.chat_ids.length === 0}
            onClick={() => test.mutate()}
          >
            {t('settings.test')}
          </Button>
        </Stack>
      </Stack>
    </Section>
  );
}

export default function SettingsPage() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: qk.admin.settings(), queryFn: settingsApi.get });
  return (
    <>
      <PageHeader title={t('settings.title')} />
      {q.isPending ? (
        <Stack spacing={2}>
          <Skeleton variant="rounded" height={120} />
          <Skeleton variant="rounded" height={320} />
        </Stack>
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <Stack spacing={2}>
          <SdCard s={q.data} />
          <SyncCard key={JSON.stringify(q.data.sync_intervals)} s={q.data} />
          <PhoneCard key={JSON.stringify(q.data.phone_max_clients)} s={q.data} />
          <TelegramCard key={JSON.stringify(q.data.telegram)} s={q.data} />
        </Stack>
      )}
    </>
  );
}

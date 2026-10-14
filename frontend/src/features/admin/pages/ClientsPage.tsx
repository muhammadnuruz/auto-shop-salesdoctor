import BlockIcon from '@mui/icons-material/Block';
import LockOpenIcon from '@mui/icons-material/LockOpen';
import LogoutIcon from '@mui/icons-material/Logout';
import SearchIcon from '@mui/icons-material/Search';
import Chip from '@mui/material/Chip';
import IconButton from '@mui/material/IconButton';
import InputAdornment from '@mui/material/InputAdornment';
import MenuItem from '@mui/material/MenuItem';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import Tab from '@mui/material/Tab';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TablePagination from '@mui/material/TablePagination';
import TableRow from '@mui/material/TableRow';
import Tabs from '@mui/material/Tabs';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogContentText from '@mui/material/DialogContentText';
import DialogTitle from '@mui/material/DialogTitle';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSnackbar } from 'notistack';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router-dom';
import { clientsApi } from '@/api/admin';
import { qk } from '@/api/keys';
import type { AdminClient, PhoneFlag, PhoneOverrideStatus, PhoneStatus } from '@/api/types';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { PageHeader } from '@/components/PageHeader';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/States';
import { useDebounced } from '@/hooks/useDebounced';
import { usePaging } from '@/hooks/usePaging';
import { formatDateTime } from '@/lib/format';
import { formatPhoneKey } from '@/lib/phone';
import { useCan } from '../AdminContext';

function Pager({ total, paging }: { total: number; paging: ReturnType<typeof usePaging> }) {
  const { t } = useTranslation();
  return (
    <TablePagination
      component="div"
      count={total}
      page={paging.page}
      rowsPerPage={paging.pageSize}
      onPageChange={(_, p) => paging.setPage(p)}
      onRowsPerPageChange={(e) => paging.setPageSize(Number(e.target.value))}
      rowsPerPageOptions={[10, 25, 50, 100]}
      labelRowsPerPage={t('common.rowsPerPage')}
      labelDisplayedRows={({ from, to, count }) => `${from}–${to} ${t('common.of')} ${count}`}
    />
  );
}

type ClientAction = { kind: 'block' | 'unblock' | 'revoke'; client: AdminClient } | null;

function ClientsTab() {
  const { t } = useTranslation();
  const can = useCan();
  const qc = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  const paging = usePaging();
  const [search, setSearch] = useState('');
  const [blockedFilter, setBlockedFilter] = useState<'all' | 'blocked' | 'unblocked'>('all');
  const [action, setAction] = useState<ClientAction>(null);
  const [reason, setReason] = useState('');
  const q = useDebounced(search.trim());
  const canWrite = can('clients.write');

  const params = {
    q: q || undefined,
    blocked: blockedFilter === 'all' ? undefined : blockedFilter === 'blocked',
    limit: paging.pageSize,
    offset: paging.offset,
  };
  const list = useQuery({
    queryKey: qk.admin.clients.list(params),
    queryFn: () => clientsApi.list(params),
    placeholderData: keepPreviousData,
  });

  const run = useMutation({
    mutationFn: async (a: NonNullable<ClientAction>) => {
      if (a.kind === 'block')
        return { kind: a.kind, res: await clientsApi.block(a.client.id, reason.trim() || null) };
      if (a.kind === 'unblock') return { kind: a.kind, res: await clientsApi.unblock(a.client.id) };
      return { kind: a.kind, res: await clientsApi.revokeSessions(a.client.id) };
    },
    onSuccess: async ({ kind, res }) => {
      if (kind === 'revoke')
        enqueueSnackbar(t('clients.revokedOk', { count: (res as { revoked: number }).revoked }), {
          variant: 'success',
        });
      else
        enqueueSnackbar(t(kind === 'block' ? 'clients.blockedOk' : 'clients.unblockedOk'), {
          variant: 'success',
        });
      setAction(null);
      setReason('');
      await qc.invalidateQueries({ queryKey: qk.admin.clients.all() });
    },
  });

  const items = list.data?.items ?? [];
  const name = action?.client.name ?? action?.client.sd_id ?? '';

  return (
    <Paper variant="outlined">
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} sx={{ p: 2 }}>
        <TextField
          label={t('common.search')}
          placeholder={t('clients.searchPlaceholder')}
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            paging.reset();
          }}
          slotProps={{
            input: {
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon fontSize="small" />
                </InputAdornment>
              ),
            },
            htmlInput: { maxLength: 100 },
          }}
          sx={{ maxWidth: { md: 360 } }}
        />
        <TextField
          select
          label={t('clients.filterBlocked')}
          value={blockedFilter}
          onChange={(e) => {
            setBlockedFilter(e.target.value as typeof blockedFilter);
            paging.reset();
          }}
          sx={{ minWidth: 200, maxWidth: { md: 240 } }}
        >
          <MenuItem value="all">{t('common.all')}</MenuItem>
          <MenuItem value="blocked">{t('clients.onlyBlocked')}</MenuItem>
          <MenuItem value="unblocked">{t('clients.onlyUnblocked')}</MenuItem>
        </TextField>
      </Stack>
      <TableContainer>
        <Table size="small" aria-label={t('clients.tabClients')}>
          <TableHead>
            <TableRow>
              <TableCell>{t('clients.colName')}</TableCell>
              <TableCell>{t('clients.colAddress')}</TableCell>
              <TableCell>{t('clients.colStatus')}</TableCell>
              {canWrite && <TableCell align="right">{t('common.actions')}</TableCell>}
            </TableRow>
          </TableHead>
          {list.isPending ? (
            <TableSkeleton cols={canWrite ? 4 : 3} />
          ) : (
            <TableBody>
              {items.map((c) => (
                <TableRow key={c.id} hover>
                  <TableCell>
                    <Typography fontWeight={600}>{c.name ?? '—'}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {c.sd_id}
                    </Typography>
                  </TableCell>
                  <TableCell>{c.address ?? '—'}</TableCell>
                  <TableCell>
                    <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
                      {c.blocked ? (
                        <Tooltip title={c.blocked_reason ?? ''}>
                          <Chip size="small" color="error" label={t('clients.blocked')} />
                        </Tooltip>
                      ) : (
                        <Chip
                          size="small"
                          color="success"
                          variant="outlined"
                          label={t('clients.ok')}
                        />
                      )}
                      {!c.active && <Chip size="small" label={t('clients.inactive')} />}
                    </Stack>
                  </TableCell>
                  {canWrite && (
                    <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                      {c.blocked ? (
                        <Tooltip title={t('clients.unblock')}>
                          <IconButton
                            size="small"
                            aria-label={`${t('clients.unblock')}: ${c.name ?? c.sd_id}`}
                            onClick={() => setAction({ kind: 'unblock', client: c })}
                          >
                            <LockOpenIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      ) : (
                        <Tooltip title={t('clients.block')}>
                          <IconButton
                            size="small"
                            aria-label={`${t('clients.block')}: ${c.name ?? c.sd_id}`}
                            onClick={() => setAction({ kind: 'block', client: c })}
                          >
                            <BlockIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      )}
                      <Tooltip title={t('clients.revoke')}>
                        <IconButton
                          size="small"
                          aria-label={`${t('clients.revoke')}: ${c.name ?? c.sd_id}`}
                          onClick={() => setAction({ kind: 'revoke', client: c })}
                        >
                          <LogoutIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          )}
        </Table>
      </TableContainer>
      {list.isError && <ErrorState error={list.error} onRetry={() => void list.refetch()} />}
      {list.isSuccess && items.length === 0 && (
        <EmptyState title={t('clients.empty')} text={t('clients.emptyText')} />
      )}
      <Pager total={list.data?.total ?? 0} paging={paging} />

      <Dialog
        open={action?.kind === 'block'}
        onClose={() => setAction(null)}
        fullWidth
        maxWidth="xs"
      >
        <DialogTitle>{t('clients.blockTitle')}</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>{t('clients.blockText', { name })}</DialogContentText>
          <TextField
            autoFocus
            label={t('clients.reasonLabel')}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            multiline
            minRows={2}
            slotProps={{ htmlInput: { maxLength: 500 } }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAction(null)} disabled={run.isPending}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="contained"
            color="error"
            disabled={run.isPending}
            onClick={() => action && run.mutate(action)}
          >
            {t('clients.block')}
          </Button>
        </DialogActions>
      </Dialog>
      <ConfirmDialog
        open={action?.kind === 'unblock' || action?.kind === 'revoke'}
        destructive={action?.kind === 'revoke'}
        loading={run.isPending}
        title={action?.kind === 'unblock' ? t('clients.unblock') : t('clients.revoke')}
        message={
          action
            ? t(action.kind === 'unblock' ? 'clients.unblockConfirm' : 'clients.revokeConfirm', {
                name,
              })
            : undefined
        }
        onClose={() => setAction(null)}
        onConfirm={() => action && run.mutate(action)}
      />
    </Paper>
  );
}

const PHONE_COLORS: Record<PhoneStatus, 'default' | 'warning' | 'error' | 'success'> = {
  ok: 'default',
  suspicious: 'warning',
  blocked: 'error',
  allowed: 'success',
};

function PhonesTab() {
  const { t } = useTranslation();
  const can = useCan();
  const qc = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  const paging = usePaging();
  const [status, setStatus] = useState<PhoneStatus | 'all'>('suspicious');
  const [search, setSearch] = useState('');
  const [target, setTarget] = useState<PhoneFlag | null>(null);
  const [next, setNext] = useState<PhoneOverrideStatus>('allowed');
  const [reason, setReason] = useState('');
  const q = useDebounced(search.trim());
  const canWrite = can('clients.write');

  const params = {
    status: status === 'all' ? undefined : status,
    q: q || undefined,
    limit: paging.pageSize,
    offset: paging.offset,
  };
  const list = useQuery({
    queryKey: qk.admin.phones.list(params),
    queryFn: () => clientsApi.phones(params),
    placeholderData: keepPreviousData,
  });

  const change = useMutation({
    mutationFn: () =>
      clientsApi.overridePhone(target!.phone_key, { status: next, reason: reason.trim() || null }),
    onSuccess: async () => {
      enqueueSnackbar(t('clients.phoneChanged'), { variant: 'success' });
      setTarget(null);
      setReason('');
      await qc.invalidateQueries({ queryKey: qk.admin.phones.all() });
    },
  });

  const items = list.data?.items ?? [];
  return (
    <Paper variant="outlined">
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} sx={{ p: 2 }}>
        <TextField
          select
          label={t('clients.phonesFilter')}
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as typeof status);
            paging.reset();
          }}
          sx={{ minWidth: 200, maxWidth: { md: 240 } }}
        >
          <MenuItem value="all">{t('common.all')}</MenuItem>
          {(['suspicious', 'blocked', 'allowed', 'ok'] as const).map((s) => (
            <MenuItem key={s} value={s}>
              {t(`clients.phone_${s}`)}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          label={t('clients.phoneSearch')}
          value={search}
          onChange={(e) => {
            setSearch(e.target.value.replace(/\D/g, '').slice(0, 9));
            paging.reset();
          }}
          slotProps={{
            htmlInput: { inputMode: 'numeric' },
            input: { startAdornment: <InputAdornment position="start">+998</InputAdornment> },
          }}
          sx={{ maxWidth: { md: 300 } }}
        />
      </Stack>
      <TableContainer>
        <Table size="small" aria-label={t('clients.tabPhones')}>
          <TableHead>
            <TableRow>
              <TableCell>{t('clients.colPhone')}</TableCell>
              <TableCell>{t('clients.colStatus')}</TableCell>
              <TableCell align="right">{t('clients.colClients')}</TableCell>
              <TableCell>{t('common.reason')}</TableCell>
              <TableCell>{t('clients.colUpdated')}</TableCell>
              {canWrite && <TableCell align="right">{t('common.actions')}</TableCell>}
            </TableRow>
          </TableHead>
          {list.isPending ? (
            <TableSkeleton cols={canWrite ? 6 : 5} />
          ) : (
            <TableBody>
              {items.map((p) => (
                <TableRow key={p.phone_key} hover>
                  <TableCell sx={{ whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
                    {formatPhoneKey(p.phone_key)}
                  </TableCell>
                  <TableCell>
                    <Chip
                      size="small"
                      color={PHONE_COLORS[p.status]}
                      label={t(`clients.phone_${p.status}`)}
                    />
                  </TableCell>
                  <TableCell align="right">{p.client_count}</TableCell>
                  <TableCell>{p.reason ?? '—'}</TableCell>
                  <TableCell sx={{ whiteSpace: 'nowrap' }}>
                    {formatDateTime(p.updated_at)}
                  </TableCell>
                  {canWrite && (
                    <TableCell align="right">
                      <Button
                        size="small"
                        onClick={() => {
                          setTarget(p);
                          setNext(p.status === 'allowed' ? 'blocked' : 'allowed');
                        }}
                      >
                        {t('clients.changeStatus')}
                      </Button>
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          )}
        </Table>
      </TableContainer>
      {list.isError && <ErrorState error={list.error} onRetry={() => void list.refetch()} />}
      {list.isSuccess && items.length === 0 && <EmptyState title={t('clients.phoneEmpty')} />}
      <Pager total={list.data?.total ?? 0} paging={paging} />

      <Dialog open={!!target} onClose={() => setTarget(null)} fullWidth maxWidth="xs">
        <DialogTitle>
          {t('clients.phoneDialogTitle', { phone: target ? formatPhoneKey(target.phone_key) : '' })}
        </DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <TextField
              select
              label={t('clients.changeStatus')}
              value={next}
              onChange={(e) => setNext(e.target.value as PhoneOverrideStatus)}
            >
              <MenuItem value="allowed">{t('clients.setAllowed')}</MenuItem>
              <MenuItem value="blocked">{t('clients.setBlocked')}</MenuItem>
              <MenuItem value="auto">{t('clients.setAuto')}</MenuItem>
            </TextField>
            {next !== 'auto' && (
              <TextField
                label={t('clients.reasonLabel')}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                slotProps={{ htmlInput: { maxLength: 500 } }}
              />
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setTarget(null)} disabled={change.isPending}>
            {t('common.cancel')}
          </Button>
          <Button variant="contained" disabled={change.isPending} onClick={() => change.mutate()}>
            {t('common.save')}
          </Button>
        </DialogActions>
      </Dialog>
    </Paper>
  );
}

export default function ClientsPage() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') === 'phones' ? 'phones' : 'clients';
  return (
    <>
      <PageHeader title={t('clients.title')} />
      <Tabs
        value={tab}
        onChange={(_, v: string) =>
          setParams(v === 'phones' ? { tab: 'phones' } : {}, { replace: true })
        }
        sx={{ mb: 2 }}
        aria-label={t('clients.title')}
      >
        <Tab value="clients" label={t('clients.tabClients')} />
        <Tab value="phones" label={t('clients.tabPhones')} />
      </Tabs>
      {tab === 'clients' ? <ClientsTab /> : <PhonesTab />}
    </>
  );
}

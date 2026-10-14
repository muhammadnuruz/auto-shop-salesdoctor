import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import CircularProgress from '@mui/material/CircularProgress';
import Paper from '@mui/material/Paper';
import Skeleton from '@mui/material/Skeleton';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Typography from '@mui/material/Typography';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSnackbar } from 'notistack';
import { useTranslation } from 'react-i18next';
import { syncApi } from '@/api/admin';
import { qk } from '@/api/keys';
import { PageHeader } from '@/components/PageHeader';
import { ErrorState, TableSkeleton } from '@/components/States';
import { formatDateTime, formatDuration } from '@/lib/format';
import { useCan } from '../AdminContext';

const REFRESH_MS = 10_000;

function statusColor(s: string): 'success' | 'error' | 'warning' | 'default' | 'info' {
  if (['success', 'ok'].includes(s)) return 'success';
  if (['error', 'failed'].includes(s)) return 'error';
  if (['partial', 'skipped'].includes(s)) return 'warning';
  if (s === 'running') return 'info';
  return 'default';
}

function StatusChip({ status }: { status: string }) {
  const { t } = useTranslation();
  return (
    <Chip
      size="small"
      color={statusColor(status)}
      label={t(`sync.st_${status}`, { defaultValue: status })}
    />
  );
}

function HealthCard() {
  const { t } = useTranslation();
  const health = useQuery({
    queryKey: qk.admin.sync.health(),
    queryFn: syncApi.health,
    refetchInterval: 30_000,
  });
  if (health.isPending) return <Skeleton variant="rounded" height={48} />;
  if (health.isError)
    return <ErrorState error={health.error} onRetry={() => void health.refetch()} />;
  const ok = health.data.status === 'ok';
  return (
    <Alert severity={ok ? 'success' : 'error'} role="status">
      {t('sync.health')}: <b>{ok ? t('sync.healthOk') : t('sync.healthError')}</b>
      {health.data.latency_ms != null && ` · ${t('sync.latency', { ms: health.data.latency_ms })}`}
      {!ok && health.data.message ? ` · ${health.data.message}` : ''}
    </Alert>
  );
}

export default function SyncPage() {
  const { t } = useTranslation();
  const can = useCan();
  const qc = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  const canRun = can('sync.run');

  const status = useQuery({
    queryKey: qk.admin.sync.status(),
    queryFn: syncApi.status,
    refetchInterval: REFRESH_MS,
  });
  const run = useMutation({
    mutationFn: (entity: string) => syncApi.run(entity),
    onSuccess: async (res) => {
      enqueueSnackbar(
        t('sync.runDone', {
          entity: t(`sync.entities.${res.entity}`, { defaultValue: res.entity }),
          status: t(`sync.st_${res.status}`, { defaultValue: res.status }),
        }),
        { variant: res.status === 'error' || res.status === 'failed' ? 'error' : 'success' },
      );
      await qc.invalidateQueries({ queryKey: qk.admin.sync.status() });
    },
  });

  const entities = status.data?.entities ?? [];
  const cols = canRun ? 7 : 6;

  return (
    <>
      <PageHeader title={t('sync.title')} subtitle={t('sync.autoRefresh')} />
      <Stack spacing={2}>
        {can('sync.read') && <HealthCard />}
        <Paper variant="outlined">
          <TableContainer>
            <Table size="small" aria-label={t('sync.title')}>
              <TableHead>
                <TableRow>
                  <TableCell>{t('sync.entity')}</TableCell>
                  <TableCell>{t('sync.status')}</TableCell>
                  <TableCell align="right">{t('sync.items')}</TableCell>
                  <TableCell align="right">{t('sync.warnings')}</TableCell>
                  <TableCell align="right">{t('sync.duration')}</TableCell>
                  <TableCell>{t('sync.lastRun')}</TableCell>
                  {canRun && <TableCell align="right">{t('common.actions')}</TableCell>}
                </TableRow>
              </TableHead>
              {status.isPending ? (
                <TableSkeleton cols={cols} />
              ) : (
                <TableBody>
                  {entities.map((e) => {
                    const r = status.data?.latest[e];
                    const running = run.isPending && run.variables === e;
                    return (
                      <TableRow key={e} hover>
                        <TableCell>
                          <Typography fontWeight={600}>
                            {t(`sync.entities.${e}`, { defaultValue: e })}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            {e}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          {r ? (
                            <StatusChip status={r.status} />
                          ) : (
                            <Typography color="text.secondary">{t('sync.never')}</Typography>
                          )}
                          {r?.error && (
                            <Typography
                              variant="caption"
                              color="error"
                              display="block"
                              sx={{ maxWidth: 320 }}
                              noWrap
                              title={r.error}
                            >
                              {r.error}
                            </Typography>
                          )}
                        </TableCell>
                        <TableCell align="right">{r?.items ?? '—'}</TableCell>
                        <TableCell align="right">{r?.warnings ?? '—'}</TableCell>
                        <TableCell align="right">
                          {r ? formatDuration(r.duration_ms) : '—'}
                        </TableCell>
                        <TableCell sx={{ whiteSpace: 'nowrap' }}>
                          {r ? formatDateTime(r.started_at) : '—'}
                        </TableCell>
                        {canRun && (
                          <TableCell align="right">
                            <Button
                              size="small"
                              variant="outlined"
                              disabled={run.isPending}
                              onClick={() => run.mutate(e)}
                              startIcon={
                                running ? <CircularProgress size={14} /> : <PlayArrowIcon />
                              }
                              aria-label={`${t('sync.runNow')}: ${e}`}
                            >
                              {running ? t('sync.running') : t('sync.runNow')}
                            </Button>
                          </TableCell>
                        )}
                      </TableRow>
                    );
                  })}
                </TableBody>
              )}
            </Table>
          </TableContainer>
          {status.isError && (
            <ErrorState error={status.error} onRetry={() => void status.refetch()} />
          )}
        </Paper>

        {status.data && status.data.recent.length > 0 && (
          <Paper variant="outlined">
            <Typography variant="h3" component="h2" sx={{ p: 2, pb: 0 }}>
              {t('sync.recent')}
            </Typography>
            <TableContainer>
              <Table size="small" aria-label={t('sync.recent')}>
                <TableBody>
                  {status.data.recent.slice(0, 10).map((r) => (
                    <TableRow key={r.id}>
                      <TableCell>
                        {t(`sync.entities.${r.entity}`, { defaultValue: r.entity })}
                      </TableCell>
                      <TableCell>
                        <StatusChip status={r.status} />
                      </TableCell>
                      <TableCell align="right">{r.items}</TableCell>
                      <TableCell align="right">{formatDuration(r.duration_ms)}</TableCell>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>
                        {formatDateTime(r.started_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </Paper>
        )}
      </Stack>
    </>
  );
}

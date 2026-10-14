import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import MenuItem from '@mui/material/MenuItem';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TablePagination from '@mui/material/TablePagination';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { auditApi, type AuditFilters } from '@/api/admin';
import { qk } from '@/api/keys';
import type { AuditEntry } from '@/api/types';
import { PageHeader } from '@/components/PageHeader';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/States';
import { useDebounced } from '@/hooks/useDebounced';
import { usePaging } from '@/hooks/usePaging';
import { formatDateTime, localInputToIso } from '@/lib/format';

type ActorType = NonNullable<AuditFilters['actor_type']>;
const ACTOR_TYPES: ActorType[] = ['admin', 'shop', 'system'];
const COLS = 5;

interface FilterState {
  actorType: ActorType | '';
  actorId: string;
  action: string;
  entityType: string;
  entityId: string;
  from: string;
  to: string;
}

const EMPTY: FilterState = {
  actorType: '',
  actorId: '',
  action: '',
  entityType: '',
  entityId: '',
  from: '',
  to: '',
};

function toQuery(f: FilterState): AuditFilters {
  return {
    actor_type: f.actorType || null,
    actor_id: f.actorId ? Number(f.actorId) : null,
    action: f.action || null,
    entity_type: f.entityType || null,
    entity_id: f.entityId.trim() || null,
    date_from: localInputToIso(f.from),
    date_to: localInputToIso(f.to),
  };
}

function Json({ value }: { value: unknown }) {
  if (value == null) return <Typography color="text.secondary">—</Typography>;
  return (
    <Box
      component="pre"
      sx={{
        m: 0,
        p: 1,
        bgcolor: 'action.hover',
        borderRadius: 1,
        fontSize: 12,
        overflowX: 'auto',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
      }}
    >
      {JSON.stringify(value, null, 2)}
    </Box>
  );
}

function DetailsDialog({ entry, onClose }: { entry: AuditEntry | null; onClose: () => void }) {
  const { t } = useTranslation();
  return (
    <Dialog open={entry != null} onClose={onClose} fullWidth maxWidth="md">
      {entry && (
        <>
          <DialogTitle>
            {entry.action} · {formatDateTime(entry.at)}
          </DialogTitle>
          <DialogContent dividers>
            <Stack spacing={1.5}>
              <Typography variant="body2">
                {t('audit.colEntity')}: {entry.entity_type ?? '—'} {entry.entity_id ?? ''}
              </Typography>
              <Typography variant="body2">
                {t('audit.colIp')}: {entry.ip ?? '—'} · {t('audit.requestId')}:{' '}
                {entry.request_id ?? '—'}
              </Typography>
              {entry.user_agent && (
                <Typography variant="body2" color="text.secondary">
                  {t('audit.userAgent')}: {entry.user_agent}
                </Typography>
              )}
              <Typography variant="subtitle2">{t('audit.before')}</Typography>
              <Json value={entry.before} />
              <Typography variant="subtitle2">{t('audit.after')}</Typography>
              <Json value={entry.after} />
            </Stack>
          </DialogContent>
          <DialogActions>
            <Button onClick={onClose}>{t('common.close')}</Button>
          </DialogActions>
        </>
      )}
    </Dialog>
  );
}

export default function AuditPage() {
  const { t } = useTranslation();
  const paging = usePaging(50);
  const [filters, setFilters] = useState<FilterState>(EMPTY);
  const [selected, setSelected] = useState<AuditEntry | null>(null);
  const debouncedEntityId = useDebounced(filters.entityId, 400);
  const query = toQuery({ ...filters, entityId: debouncedEntityId });

  const facets = useQuery({ queryKey: qk.admin.audit.facets(), queryFn: auditApi.facets });
  const list = useQuery({
    queryKey: qk.admin.audit.list({ ...query, limit: paging.pageSize, offset: paging.offset }),
    queryFn: () => auditApi.list({ ...query, limit: paging.pageSize, offset: paging.offset }),
    placeholderData: keepPreviousData,
  });

  const set = (patch: Partial<FilterState>) => {
    setFilters((f) => ({ ...f, ...patch }));
    paging.reset();
  };

  const actorLabel = (e: AuditEntry) => {
    if (e.actor_type === 'admin') return e.actor_name ?? `#${e.actor_id ?? '?'}`;
    if (e.actor_type === 'system') return t('audit.system');
    return `${t('audit.actorTypes.shop')} #${e.actor_id ?? '?'}`;
  };

  return (
    <>
      <PageHeader title={t('audit.title')} />
      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Box
          sx={{
            display: 'grid',
            gap: 1.5,
            gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, 1fr)', md: 'repeat(4, 1fr)' },
          }}
        >
          <TextField
            select
            size="small"
            label={t('audit.actorType')}
            value={filters.actorType}
            onChange={(e) => set({ actorType: e.target.value as ActorType | '' })}
          >
            <MenuItem value="">{t('audit.all')}</MenuItem>
            {ACTOR_TYPES.map((a) => (
              <MenuItem key={a} value={a}>
                {t(`audit.actorTypes.${a}`)}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            size="small"
            label={t('audit.actor')}
            value={filters.actorId}
            onChange={(e) =>
              set({ actorId: e.target.value, actorType: e.target.value ? 'admin' : '' })
            }
          >
            <MenuItem value="">{t('audit.all')}</MenuItem>
            {(facets.data?.actors ?? []).map((a) => (
              <MenuItem key={String(a.id)} value={String(a.id)}>
                {String(a.username)}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            size="small"
            label={t('audit.action')}
            value={filters.action}
            onChange={(e) => set({ action: e.target.value })}
          >
            <MenuItem value="">{t('audit.all')}</MenuItem>
            {(facets.data?.actions ?? []).map((a) => (
              <MenuItem key={a} value={a}>
                {a}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            size="small"
            label={t('audit.entityType')}
            value={filters.entityType}
            onChange={(e) => set({ entityType: e.target.value })}
          >
            <MenuItem value="">{t('audit.all')}</MenuItem>
            {(facets.data?.entity_types ?? []).map((a) => (
              <MenuItem key={a} value={a}>
                {a}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            size="small"
            label={t('audit.entityId')}
            value={filters.entityId}
            onChange={(e) => set({ entityId: e.target.value })}
            inputProps={{ maxLength: 100 }}
          />
          <TextField
            size="small"
            type="datetime-local"
            label={t('audit.from')}
            value={filters.from}
            onChange={(e) => set({ from: e.target.value })}
            InputLabelProps={{ shrink: true }}
          />
          <TextField
            size="small"
            type="datetime-local"
            label={t('audit.to')}
            value={filters.to}
            onChange={(e) => set({ to: e.target.value })}
            InputLabelProps={{ shrink: true }}
          />
          <Button onClick={() => set(EMPTY)} variant="text">
            {t('common.reset')}
          </Button>
        </Box>
      </Paper>
      <Paper variant="outlined">
        {list.isError ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : (
          <>
            <TableContainer>
              <Table size="small" aria-label={t('audit.title')}>
                <TableHead>
                  <TableRow>
                    <TableCell>{t('audit.colAt')}</TableCell>
                    <TableCell>{t('audit.colActor')}</TableCell>
                    <TableCell>{t('audit.colAction')}</TableCell>
                    <TableCell>{t('audit.colEntity')}</TableCell>
                    <TableCell>{t('audit.colIp')}</TableCell>
                  </TableRow>
                </TableHead>
                {list.isPending ? (
                  <TableSkeleton cols={COLS} />
                ) : (
                  <TableBody>
                    {list.data.items.map((e) => (
                      <TableRow
                        key={e.id}
                        hover
                        sx={{ cursor: 'pointer' }}
                        onClick={() => setSelected(e)}
                        tabIndex={0}
                        onKeyDown={(ev) => {
                          if (ev.key === 'Enter') setSelected(e);
                        }}
                        aria-label={`${t('audit.details')}: ${e.action}`}
                      >
                        <TableCell sx={{ whiteSpace: 'nowrap' }}>{formatDateTime(e.at)}</TableCell>
                        <TableCell>{actorLabel(e)}</TableCell>
                        <TableCell>
                          <code>{e.action}</code>
                        </TableCell>
                        <TableCell>
                          {e.entity_type ?? '—'}
                          {e.entity_id ? ` #${e.entity_id}` : ''}
                        </TableCell>
                        <TableCell>{e.ip ?? '—'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                )}
              </Table>
            </TableContainer>
            {list.data && list.data.total === 0 && <EmptyState title={t('audit.empty')} />}
            <TablePagination
              component="div"
              count={list.data?.total ?? 0}
              page={paging.page}
              rowsPerPage={paging.pageSize}
              onPageChange={(_, p) => paging.setPage(p)}
              onRowsPerPageChange={(e) => paging.setPageSize(Number(e.target.value))}
              rowsPerPageOptions={[25, 50, 100, 200]}
            />
          </>
        )}
      </Paper>
      <DetailsDialog entry={selected} onClose={() => setSelected(null)} />
    </>
  );
}

import DownloadIcon from '@mui/icons-material/Download';
import Autocomplete from '@mui/material/Autocomplete';
import Button from '@mui/material/Button';
import Checkbox from '@mui/material/Checkbox';
import ListItemText from '@mui/material/ListItemText';
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
import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query';
import { useSnackbar } from 'notistack';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link as RouterLink, useSearchParams } from 'react-router-dom';
import { adminOrdersApi, clientsApi, linksApi, type AdminOrderFilters } from '@/api/admin';
import { qk } from '@/api/keys';
import { ORDER_STATES, type OrderState } from '@/api/types';
import { PageHeader } from '@/components/PageHeader';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/States';
import { StateChip } from '@/features/orders/StateChip';
import { useDebounced } from '@/hooks/useDebounced';
import { usePaging } from '@/hooks/usePaging';
import { describeError } from '@/lib/errors';
import { formatAmount, formatDateTime } from '@/lib/format';
import { saveBlob } from '@/lib/download';
import { useCan } from '../AdminContext';
import { useRefs } from '../queries';

const SD_STATUSES = [1, 2, 3, 4, 5];

interface Opt {
  id: number;
  name: string;
}

function parseFilters(sp: URLSearchParams): AdminOrderFilters {
  const num = (k: string) => {
    const v = sp.get(k);
    return v && /^\d+$/.test(v) ? Number(v) : null;
  };
  return {
    state: sp
      .getAll('state')
      .filter((s): s is OrderState => (ORDER_STATES as string[]).includes(s)),
    sd_status: sp
      .getAll('sd_status')
      .map(Number)
      .filter((n) => SD_STATUSES.includes(n)),
    client: num('client'),
    link: num('link'),
    warehouse: sp.get('warehouse') ?? undefined,
    agent: sp.get('agent') ?? undefined,
    date_from: sp.get('date_from') ?? undefined,
    date_to: sp.get('date_to') ?? undefined,
    q: sp.get('q') ?? undefined,
  };
}

function toParams(f: AdminOrderFilters): URLSearchParams {
  const sp = new URLSearchParams();
  for (const s of f.state ?? []) sp.append('state', s);
  for (const s of f.sd_status ?? []) sp.append('sd_status', String(s));
  for (const k of ['client', 'link', 'warehouse', 'agent', 'date_from', 'date_to', 'q'] as const) {
    const v = f[k];
    if (v !== undefined && v !== null && v !== '') sp.set(k, String(v));
  }
  return sp;
}

function EntityFilter({
  label,
  value,
  fetchOptions,
  onChange,
}: {
  label: string;
  value: number | null;
  fetchOptions: (q: string) => Promise<Opt[]>;
  onChange: (id: number | null) => void;
}) {
  const [input, setInput] = useState('');
  const [picked, setPicked] = useState<Opt | null>(null);
  const q = useDebounced(input.trim(), 250);
  const opts = useQuery({
    queryKey: ['admin', 'order-filter', label, q],
    queryFn: () => fetchOptions(q),
    staleTime: 30_000,
  });
  const current: Opt | null =
    value === null ? null : picked?.id === value ? picked : { id: value, name: `#${value}` };
  return (
    <Autocomplete<Opt, false, false, false>
      options={opts.data ?? []}
      loading={opts.isFetching}
      value={current}
      isOptionEqualToValue={(a, b) => a.id === b.id}
      getOptionLabel={(o) => o.name}
      filterOptions={(o) => o}
      onInputChange={(_, v, reason) => reason === 'input' && setInput(v)}
      onChange={(_, v) => {
        setPicked(v);
        onChange(v?.id ?? null);
      }}
      sx={{ minWidth: 200 }}
      renderInput={(params) => <TextField {...params} label={label} />}
    />
  );
}

export default function AdminOrdersPage() {
  const { t } = useTranslation();
  const can = useCan();
  const { enqueueSnackbar } = useSnackbar();
  const refs = useRefs();
  const [sp, setSp] = useSearchParams();
  const paging = usePaging(25);
  const filters = parseFilters(sp);
  const [search, setSearch] = useState(filters.q ?? '');
  const qDebounced = useDebounced(search.trim());

  // the search box is debounced; every other filter lives in the URL (shareable, dashboard links)
  const effective: AdminOrderFilters = { ...filters, q: qDebounced || undefined };
  const apply = (patch: Partial<AdminOrderFilters>) => {
    setSp(toParams({ ...effective, ...patch }), { replace: true });
    paging.reset();
  };

  const params = { ...effective, limit: paging.pageSize, offset: paging.offset };
  const list = useQuery({
    queryKey: qk.admin.orders.list(params),
    queryFn: () => adminOrdersApi.list(params),
    placeholderData: keepPreviousData,
  });
  const items = list.data?.items ?? [];

  const exportXlsx = useMutation({
    mutationFn: () => adminOrdersApi.exportXlsx(effective),
    meta: { silent: true },
    onSuccess: (blob) => saveBlob(blob, 'orders.xlsx'),
    onError: (e) => enqueueSnackbar(describeError(e, t), { variant: 'error' }),
  });

  const warehouses = refs.named('warehouses');
  const agents = refs.named('agents');

  return (
    <>
      <PageHeader
        title={t('nav.orders')}
        actions={
          <Button
            variant="outlined"
            startIcon={<DownloadIcon />}
            onClick={() => exportXlsx.mutate()}
            disabled={exportXlsx.isPending || !can('orders.read')}
          >
            {t('aorders.export')}
          </Button>
        }
      />
      <Paper variant="outlined">
        <Stack direction="row" spacing={1.5} useFlexGap flexWrap="wrap" sx={{ p: 2 }}>
          <TextField
            select
            label={t('aorders.fState')}
            value={filters.state ?? []}
            onChange={(e) => apply({ state: e.target.value as unknown as OrderState[] })}
            sx={{ minWidth: 200 }}
            slotProps={{
              select: {
                multiple: true,
                renderValue: (v) => (v as string[]).map((s) => t(`orderState.${s}`)).join(', '),
              },
            }}
          >
            {ORDER_STATES.map((s) => (
              <MenuItem key={s} value={s}>
                <Checkbox checked={(filters.state ?? []).includes(s)} />
                <ListItemText primary={t(`orderState.${s}`)} />
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            label={t('aorders.fSdStatus')}
            value={filters.sd_status ?? []}
            onChange={(e) => apply({ sd_status: e.target.value as unknown as number[] })}
            sx={{ minWidth: 180 }}
            slotProps={{
              select: {
                multiple: true,
                renderValue: (v) => (v as number[]).map((s) => t(`sdStatus.s${s}`)).join(', '),
              },
            }}
          >
            {SD_STATUSES.map((s) => (
              <MenuItem key={s} value={s}>
                <Checkbox checked={(filters.sd_status ?? []).includes(s)} />
                <ListItemText primary={t(`sdStatus.s${s}`)} />
              </MenuItem>
            ))}
          </TextField>
          <EntityFilter
            label={t('aorders.fClient')}
            value={filters.client ?? null}
            fetchOptions={async (q) =>
              (await clientsApi.list({ q, limit: 20, offset: 0 })).items.map((c) => ({
                id: c.id,
                name: c.name ?? c.sd_id,
              }))
            }
            onChange={(client) => apply({ client })}
          />
          <EntityFilter
            label={t('aorders.fLink')}
            value={filters.link ?? null}
            fetchOptions={async (q) =>
              (await linksApi.list({ q, limit: 20, offset: 0 })).items.map((l) => ({
                id: l.id,
                name: l.name,
              }))
            }
            onChange={(link) => apply({ link })}
          />
          {refs.available ? (
            <>
              <TextField
                select
                label={t('aorders.fWarehouse')}
                value={filters.warehouse ?? ''}
                onChange={(e) => apply({ warehouse: e.target.value || undefined })}
                sx={{ minWidth: 180 }}
              >
                <MenuItem value="">{t('common.all')}</MenuItem>
                {warehouses.map((w) => (
                  <MenuItem key={w.id} value={w.id}>
                    {w.name ?? w.id}
                  </MenuItem>
                ))}
              </TextField>
              <TextField
                select
                label={t('aorders.fAgent')}
                value={filters.agent ?? ''}
                onChange={(e) => apply({ agent: e.target.value || undefined })}
                sx={{ minWidth: 180 }}
              >
                <MenuItem value="">{t('common.all')}</MenuItem>
                {agents.map((a) => (
                  <MenuItem key={a.id} value={a.id}>
                    {a.name ?? a.id}
                  </MenuItem>
                ))}
              </TextField>
            </>
          ) : (
            <>
              <TextField
                label={t('aorders.fWarehouse')}
                value={filters.warehouse ?? ''}
                onChange={(e) => apply({ warehouse: e.target.value || undefined })}
              />
              <TextField
                label={t('aorders.fAgent')}
                value={filters.agent ?? ''}
                onChange={(e) => apply({ agent: e.target.value || undefined })}
              />
            </>
          )}
          <TextField
            type="date"
            label={t('orders.dateFrom')}
            value={filters.date_from ?? ''}
            onChange={(e) => apply({ date_from: e.target.value || undefined })}
            slotProps={{ inputLabel: { shrink: true } }}
          />
          <TextField
            type="date"
            label={t('orders.dateTo')}
            value={filters.date_to ?? ''}
            onChange={(e) => apply({ date_to: e.target.value || undefined })}
            slotProps={{ inputLabel: { shrink: true } }}
          />
          <TextField
            label={t('aorders.fSearch')}
            placeholder={t('aorders.fSearchHint')}
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              paging.reset();
            }}
            slotProps={{ htmlInput: { maxLength: 100 } }}
            sx={{ minWidth: 220 }}
          />
          <Button
            onClick={() => {
              setSearch('');
              setSp(new URLSearchParams(), { replace: true });
              paging.reset();
            }}
          >
            {t('common.reset')}
          </Button>
        </Stack>

        {list.data && (
          <Stack
            direction="row"
            spacing={3}
            sx={{ px: 2, pb: 1 }}
            useFlexGap
            flexWrap="wrap"
            data-testid="orders-summary"
          >
            <Typography>
              {t('aorders.sumCount')}: <b>{list.data.summary.count}</b>
            </Typography>
            <Typography>
              {t('aorders.sumQty')}: <b>{formatAmount(list.data.summary.total_qty)}</b>
            </Typography>
            <Typography>
              {t('aorders.sumTotal')}:{' '}
              <b>
                {formatAmount(list.data.summary.total_sum)} {t('common.currency')}
              </b>
            </Typography>
          </Stack>
        )}

        <TableContainer>
          <Table size="small" aria-label={t('nav.orders')}>
            <TableHead>
              <TableRow>
                <TableCell>{t('orders.colCode')}</TableCell>
                <TableCell>{t('orders.colCreated')}</TableCell>
                <TableCell>{t('aorders.colClient')}</TableCell>
                <TableCell>{t('aorders.colLink')}</TableCell>
                <TableCell>{t('orders.colState')}</TableCell>
                <TableCell align="right">{t('orders.colLines')}</TableCell>
                <TableCell align="right">{t('orders.colSum')}</TableCell>
              </TableRow>
            </TableHead>
            {list.isPending ? (
              <TableSkeleton cols={7} />
            ) : (
              <TableBody>
                {items.map((o) => (
                  <TableRow key={o.public_id} hover>
                    <TableCell>
                      <Button
                        component={RouterLink}
                        to={`/admin/orders/${o.public_id}`}
                        sx={{ fontWeight: 600 }}
                      >
                        {o.code}
                      </Button>
                      {o.sd_id && (
                        <Typography variant="caption" color="text.secondary" display="block">
                          SD {o.sd_id}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell sx={{ whiteSpace: 'nowrap' }}>
                      {formatDateTime(o.created_at)}
                    </TableCell>
                    <TableCell>{o.client.name ?? o.client.id}</TableCell>
                    <TableCell>{o.link.name}</TableCell>
                    <TableCell>
                      <StateChip state={o.state} sdStatus={o.sd_status} />
                    </TableCell>
                    <TableCell align="right">{o.lines_count}</TableCell>
                    <TableCell align="right">{formatAmount(o.total_sum)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            )}
          </Table>
        </TableContainer>
        {list.isError && <ErrorState error={list.error} onRetry={() => void list.refetch()} />}
        {list.isSuccess && items.length === 0 && <EmptyState title={t('aorders.empty')} />}
        <TablePagination
          component="div"
          count={list.data?.total ?? 0}
          page={paging.page}
          rowsPerPage={paging.pageSize}
          onPageChange={(_, p) => paging.setPage(p)}
          onRowsPerPageChange={(e) => paging.setPageSize(Number(e.target.value))}
          rowsPerPageOptions={[10, 25, 50, 100]}
          labelRowsPerPage={t('common.rowsPerPage')}
          labelDisplayedRows={({ from, to, count }) => `${from}–${to} ${t('common.of')} ${count}`}
        />
      </Paper>
    </>
  );
}

import AddShoppingCartIcon from '@mui/icons-material/AddShoppingCart';
import CancelOutlinedIcon from '@mui/icons-material/CancelOutlined';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import IconButton from '@mui/material/IconButton';
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
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import useMediaQuery from '@mui/material/useMediaQuery';
import { useTheme } from '@mui/material/styles';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSnackbar } from 'notistack';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link as RouterLink, useNavigate, useParams } from 'react-router-dom';
import { ApiError } from '@/api/http';
import { qk } from '@/api/keys';
import { ordersApi } from '@/api/shop';
import { ORDER_STATES, type OrderState, type OrderSummary } from '@/api/types';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/States';
import { StateChip } from '@/features/orders/StateChip';
import { canShopCancel, isEditableState, orderErrorMessage } from '@/features/orders/orderUtils';
import { useDebounced } from '@/hooks/useDebounced';
import { usePaging } from '@/hooks/usePaging';
import { formatAmount, formatDateTime } from '@/lib/format';
import { ShopShell } from './ShopShell';
import { useShopMe } from './queries';

type Pending = { kind: 'cancel' | 'delete'; order: OrderSummary } | null;
const SHOP_STATES = ORDER_STATES.filter((s) => s !== 'deleted');

export default function ShopOrdersPage() {
  const { t } = useTranslation();
  const { slug = '' } = useParams();
  const me = useShopMe().data;
  const navigate = useNavigate();
  const qc = useQueryClient();
  const theme = useTheme();
  const mobile = useMediaQuery(theme.breakpoints.down('sm'));
  const { enqueueSnackbar } = useSnackbar();
  const paging = usePaging(20);
  const [state, setState] = useState<OrderState | ''>('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [search, setSearch] = useState('');
  const [pending, setPending] = useState<Pending>(null);
  const q = useDebounced(search.trim());

  const params = {
    state: state ? [state] : undefined,
    date_from: from || undefined,
    date_to: to || undefined,
    q: q || undefined,
    page: paging.page + 1,
    size: paging.pageSize,
  };
  const list = useQuery({
    queryKey: qk.shop.orders.list(params),
    queryFn: () => ordersApi.list(params),
    placeholderData: keepPreviousData,
  });
  const items = list.data?.items ?? [];
  const invalidate = () => qc.invalidateQueries({ queryKey: qk.shop.orders.all() });

  const repeat = useMutation({
    mutationFn: (o: OrderSummary) => ordersApi.duplicate(o.public_id),
    onSuccess: (o) => navigate(`/s/${slug}/orders/${o.public_id}`),
  });
  const act = useMutation({
    mutationFn: async ({ kind, order }: NonNullable<Pending>) => {
      if (kind === 'delete') return ordersApi.remove(order.public_id, order.version);
      const res = await ordersApi.cancel(order.public_id, order.version);
      if (!res.ok) {
        const d = (res.data as { detail?: { code?: string } } | null)?.detail;
        throw new ApiError(res.status, d ?? `HTTP ${res.status}`, '/api/shop/orders');
      }
      return res.data;
    },
    meta: { silent: true },
    onSuccess: async (data, { kind }) => {
      // 202 (cancel: order in outcome_unknown, delete: {detail:{code:'outcome_unknown'}}):
      // SD has not confirmed yet
      const body = data as { state?: string; detail?: { code?: string } } | null;
      const pending = body?.state === 'outcome_unknown' || body?.detail?.code === 'outcome_unknown';
      enqueueSnackbar(
        pending
          ? t('orders.pendingConfirm')
          : t(kind === 'delete' ? 'orders.deleted' : 'orders.cancelled'),
        { variant: pending ? 'info' : 'success' },
      );
      setPending(null);
      await invalidate();
    },
    onError: async (e) => {
      enqueueSnackbar(orderErrorMessage(e, t), { variant: 'error' });
      setPending(null);
      await invalidate();
    },
  });

  const actions = (o: OrderSummary) => (
    <Stack direction="row" justifyContent="flex-end">
      <Tooltip title={t('orders.open')}>
        <IconButton
          component={RouterLink}
          to={`/s/${slug}/orders/${o.public_id}`}
          size="small"
          aria-label={`${t('orders.open')}: ${o.code}`}
        >
          <OpenInNewIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <Tooltip title={t('orders.repeat')}>
        <span>
          <IconButton
            size="small"
            aria-label={`${t('orders.repeat')}: ${o.code}`}
            disabled={repeat.isPending}
            onClick={() => repeat.mutate(o)}
          >
            <ContentCopyIcon fontSize="small" />
          </IconButton>
        </span>
      </Tooltip>
      {canShopCancel(o) && (
        <Tooltip title={t('orders.cancel')}>
          <IconButton
            size="small"
            aria-label={`${t('orders.cancel')}: ${o.code}`}
            onClick={() => setPending({ kind: 'cancel', order: o })}
          >
            <CancelOutlinedIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      )}
      {isEditableState(o.state) && (
        <Tooltip title={t('orders.delete')}>
          <IconButton
            size="small"
            aria-label={`${t('orders.delete')}: ${o.code}`}
            onClick={() => setPending({ kind: 'delete', order: o })}
          >
            <DeleteOutlineIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      )}
    </Stack>
  );

  return (
    <ShopShell wide title={me?.link.title} logoUrl={me?.link.logo_url} color={me?.link.color}>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        justifyContent="space-between"
        alignItems={{ sm: 'center' }}
        spacing={1}
        sx={{ mb: 2 }}
      >
        <Typography variant="h1" component="h1" sx={{ fontSize: '1.5rem' }}>
          {t('orders.title')}
        </Typography>
        <Stack direction="row" spacing={1}>
          <Button component={RouterLink} to={`/s/${slug}/home`}>
            {t('order.backHome')}
          </Button>
          <Button
            component={RouterLink}
            to={`/s/${slug}/order/new`}
            variant="contained"
            startIcon={<AddShoppingCartIcon />}
          >
            {t('shop.newOrder')}
          </Button>
        </Stack>
      </Stack>
      <Paper variant="outlined">
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5} sx={{ p: 2 }}>
          <TextField
            select
            label={t('orders.filterState')}
            value={state}
            onChange={(e) => {
              setState(e.target.value as OrderState | '');
              paging.reset();
            }}
            sx={{ minWidth: 200 }}
          >
            <MenuItem value="">{t('common.all')}</MenuItem>
            {SHOP_STATES.map((s) => (
              <MenuItem key={s} value={s}>
                {t(`orderState.${s}`)}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            type="date"
            label={t('orders.dateFrom')}
            value={from}
            onChange={(e) => {
              setFrom(e.target.value);
              paging.reset();
            }}
            slotProps={{ inputLabel: { shrink: true } }}
          />
          <TextField
            type="date"
            label={t('orders.dateTo')}
            value={to}
            onChange={(e) => {
              setTo(e.target.value);
              paging.reset();
            }}
            slotProps={{ inputLabel: { shrink: true } }}
          />
          <TextField
            label={t('orders.searchCode')}
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              paging.reset();
            }}
            slotProps={{ htmlInput: { maxLength: 40 } }}
          />
        </Stack>

        {mobile ? (
          <Stack spacing={1} sx={{ p: 1 }}>
            {items.map((o) => (
              <Card key={o.public_id} variant="outlined">
                <CardContent sx={{ pb: '12px !important' }}>
                  <Stack direction="row" justifyContent="space-between" alignItems="center">
                    <Typography fontWeight={700}>{o.code}</Typography>
                    <StateChip state={o.state} sdStatus={o.sd_status} />
                  </Stack>
                  <Typography variant="body2" color="text.secondary">
                    {formatDateTime(o.created_at)} · {o.lines_count} {t('orders.linesShort')}
                  </Typography>
                  <Typography fontWeight={600}>
                    {formatAmount(o.total_sum)} {t('common.currency')}
                  </Typography>
                  {actions(o)}
                </CardContent>
              </Card>
            ))}
          </Stack>
        ) : (
          <TableContainer>
            <Table size="small" aria-label={t('orders.title')}>
              <TableHead>
                <TableRow>
                  <TableCell>{t('orders.colCode')}</TableCell>
                  <TableCell>{t('orders.colCreated')}</TableCell>
                  <TableCell>{t('orders.colState')}</TableCell>
                  <TableCell align="right">{t('orders.colLines')}</TableCell>
                  <TableCell align="right">{t('orders.colSum')}</TableCell>
                  <TableCell align="right">{t('common.actions')}</TableCell>
                </TableRow>
              </TableHead>
              {list.isPending ? (
                <TableSkeleton cols={6} />
              ) : (
                <TableBody>
                  {items.map((o) => (
                    <TableRow key={o.public_id} hover>
                      <TableCell>
                        <Button
                          component={RouterLink}
                          to={`/s/${slug}/orders/${o.public_id}`}
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
                      <TableCell>
                        <StateChip state={o.state} sdStatus={o.sd_status} />
                      </TableCell>
                      <TableCell align="right">{o.lines_count}</TableCell>
                      <TableCell align="right">{formatAmount(o.total_sum)}</TableCell>
                      <TableCell align="right">{actions(o)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              )}
            </Table>
          </TableContainer>
        )}
        {list.isError && <ErrorState error={list.error} onRetry={() => void list.refetch()} />}
        {list.isSuccess && items.length === 0 && (
          <EmptyState
            title={t('orders.empty')}
            action={
              <Button component={RouterLink} to={`/s/${slug}/order/new`} variant="contained">
                {t('shop.newOrder')}
              </Button>
            }
          />
        )}
        <TablePagination
          component="div"
          count={list.data?.total ?? 0}
          page={paging.page}
          rowsPerPage={paging.pageSize}
          onPageChange={(_, p) => paging.setPage(p)}
          onRowsPerPageChange={(e) => paging.setPageSize(Number(e.target.value))}
          rowsPerPageOptions={[10, 20, 50]}
          labelRowsPerPage={t('common.rowsPerPage')}
          labelDisplayedRows={({ from: f, to: tt, count }) =>
            `${f}–${tt} ${t('common.of')} ${count}`
          }
        />
      </Paper>

      <ConfirmDialog
        open={!!pending}
        destructive
        loading={act.isPending}
        title={
          pending ? t(pending.kind === 'cancel' ? 'orders.cancelTitle' : 'orders.deleteTitle') : ''
        }
        message={
          pending
            ? t(pending.kind === 'cancel' ? 'orders.cancelConfirm' : 'orders.deleteConfirm', {
                code: pending.order.code,
              })
            : undefined
        }
        confirmLabel={
          pending ? t(pending.kind === 'cancel' ? 'orders.cancel' : 'orders.delete') : undefined
        }
        onClose={() => setPending(null)}
        onConfirm={() => pending && act.mutate(pending)}
      />
    </ShopShell>
  );
}

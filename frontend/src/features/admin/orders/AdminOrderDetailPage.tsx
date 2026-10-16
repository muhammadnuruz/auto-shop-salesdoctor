import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import PlaylistAddIcon from '@mui/icons-material/PlaylistAdd';
import PrintIcon from '@mui/icons-material/Print';
import ReplayIcon from '@mui/icons-material/Replay';
import SaveIcon from '@mui/icons-material/Save';
import SwapHorizIcon from '@mui/icons-material/SwapHoriz';
import Alert from '@mui/material/Alert';
import AlertTitle from '@mui/material/AlertTitle';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import MenuItem from '@mui/material/MenuItem';
import Paper from '@mui/material/Paper';
import Skeleton from '@mui/material/Skeleton';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import useMediaQuery from '@mui/material/useMediaQuery';
import { useTheme } from '@mui/material/styles';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSnackbar } from 'notistack';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link as RouterLink, useNavigate, useParams } from 'react-router-dom';
import {
  adminOrdersApi,
  productsApi,
  type AdminOrderPatch,
  type AdminOrderResponse,
} from '@/api/admin';
import { qk } from '@/api/keys';
import type {
  AdminOrder,
  CatalogItem,
  HeaderField,
  OrderLine,
  OrderProblem,
  RefItem,
} from '@/api/types';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { PageHeader } from '@/components/PageHeader';
import { ErrorState } from '@/components/States';
import { AddFromCatalogDialog, type CatalogSource } from '@/features/orders/AddFromCatalogDialog';
import { CategorySummary } from '@/features/orders/CategorySummary';
import { InvoiceDialog } from '@/features/orders/InvoiceDialog';
import { OrderLinesTable } from '@/features/orders/OrderLinesTable';
import { StateChip } from '@/features/orders/StateChip';
import {
  computeQty,
  lineFromCatalog,
  problemText,
  round2,
  splitProblems,
  sumLines,
} from '@/features/orders/orderUtils';
import { defaultSettings } from '@/features/orders/useOrderSettings';
import { useDebounced } from '@/hooks/useDebounced';
import { formatAmount, formatDateTime } from '@/lib/format';
import { useCan } from '../AdminContext';
import { useRefs } from '../queries';
import { classifyAdmin } from './adminFlow';
import { OrderTimeline } from './OrderTimeline';

const HEADER_FIELDS: HeaderField[] = ['agent', 'price_type', 'warehouse', 'trade'];
const REF_GROUP = {
  agent: 'agents',
  price_type: 'price_types',
  warehouse: 'warehouses',
  trade: 'trade_directions',
} as const;

interface Draft {
  agent: string | null;
  price_type: string | null;
  warehouse: string | null;
  trade: string | null;
  shipment_date: string | null;
  comment: string;
  lines: OrderLine[];
}

const draftOf = (o: AdminOrder): Draft => ({
  agent: o.agent?.id ?? null,
  price_type: o.price_type?.id ?? null,
  warehouse: o.warehouse?.id ?? null,
  trade: o.trade?.id ?? null,
  shipment_date: o.shipment_date,
  comment: o.comment,
  lines: o.lines,
});

export default function AdminOrderDetailPage() {
  const { t } = useTranslation();
  const { id = '' } = useParams();
  const q = useQuery({
    queryKey: qk.admin.orders.detail(id),
    queryFn: ({ signal }) => adminOrdersApi.get(id, signal),
    refetchInterval: (query) => (query.state.data?.state === 'outcome_unknown' ? 5000 : false),
  });
  return (
    <>
      <Button
        component={RouterLink}
        to="/admin/orders"
        startIcon={<ArrowBackIcon />}
        sx={{ mb: 1 }}
      >
        {t('aorders.back')}
      </Button>
      {q.isPending && <Skeleton variant="rounded" height={320} aria-busy="true" />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {/* a new version (after save / refetch) remounts the editor with fresh server data */}
      {q.data && <OrderDetail key={`${q.data.public_id}:${q.data.version}`} order={q.data} />}
    </>
  );
}

function OrderDetail({ order }: { order: AdminOrder }) {
  const { t, i18n } = useTranslation();
  const theme = useTheme();
  const mobile = useMediaQuery(theme.breakpoints.down('sm'));
  const can = useCan();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  const refs = useRefs();
  const canWrite = can('orders.write');
  const canCancel = can('orders.cancel');

  const editable =
    canWrite &&
    (order.state === 'draft' ||
      order.state === 'failed' ||
      (order.state === 'sent' && (order.sd_status === null || order.sd_status === 1)));

  const [draft, setDraft] = useState<Draft>(() => draftOf(order));
  const [problems, setProblems] = useState<OrderProblem[]>([]);
  const [dialog, setDialog] = useState<'status' | 'delete' | 'catalog' | 'invoice' | null>(null);
  const [statusValue, setStatusValue] = useState<number>(order.sd_status ?? 1);
  const [statusDate, setStatusDate] = useState('');

  const base = useMemo(() => draftOf(order), [order]);
  const headerDirty = HEADER_FIELDS.some((f) => draft[f] !== base[f]);
  const linesDirty =
    JSON.stringify(draft.lines.map((l) => [l.product_ref, l.blocks, l.pieces, l.comment ?? ''])) !==
    JSON.stringify(base.lines.map((l) => [l.product_ref, l.blocks, l.pieces, l.comment ?? '']));
  const dirty =
    headerDirty ||
    linesDirty ||
    draft.shipment_date !== base.shipment_date ||
    draft.comment !== base.comment;
  const local = sumLines(draft.lines);

  const store = (o: AdminOrder) => {
    qc.setQueryData(qk.admin.orders.detail(order.public_id), o);
    void qc.invalidateQueries({ queryKey: qk.admin.orders.all() });
  };
  const refetch = () => qc.invalidateQueries({ queryKey: qk.admin.orders.detail(order.public_id) });

  /** Shared reaction to every action response. Returns true when the order changed. */
  const handle = (res: AdminOrderResponse, okMessage: string): boolean => {
    const out = classifyAdmin(res);
    switch (out.kind) {
      case 'ok':
        setProblems([]);
        store(out.order);
        if (out.order.repriced?.length) {
          enqueueSnackbar(t('aorders.repriced', { count: out.order.repriced.length }), {
            variant: 'info',
          });
        }
        if (out.warnings.includes('cancel_after_shipment_creates_return')) {
          enqueueSnackbar(t('aorders.warnReturn'), { variant: 'warning' });
        }
        enqueueSnackbar(
          out.pending
            ? t('aorders.pending')
            : out.reconcile === 'failed'
              ? t('aorders.reconcileFailed')
              : okMessage,
          { variant: out.pending ? 'info' : out.reconcile === 'failed' ? 'error' : 'success' },
        );
        return true;
      case 'unknown':
        enqueueSnackbar(t('aorders.pending'), { variant: 'info' });
        void refetch();
        return true;
      case 'invalid':
        setProblems(out.problems);
        enqueueSnackbar(
          out.message ?? t(`orderErrors.${out.code}`, { defaultValue: t('aorders.invalid') }),
          {
            variant: 'error',
          },
        );
        return false;
      case 'unavailable':
        enqueueSnackbar(t('order.unavailable'), { variant: 'error' });
        return false;
      case 'conflict':
        enqueueSnackbar(t('orderErrors.version_conflict'), { variant: 'warning' });
        void refetch();
        return false;
      case 'forbidden':
        enqueueSnackbar(t('errors.forbidden'), { variant: 'error' });
        return false;
      case 'state':
        enqueueSnackbar(
          t(`orderErrors.${out.code}`, { defaultValue: t('orderErrors.invalid_state') }),
          {
            variant: 'warning',
          },
        );
        void refetch();
        return false;
      default:
        enqueueSnackbar(t('errors.server'), { variant: 'error' });
        return false;
    }
  };

  // the proposed change (only what differs), shared by save and the live preview
  const body = useMemo(() => {
    const b: AdminOrderPatch = { version: order.version };
    for (const f of HEADER_FIELDS) if (draft[f] !== base[f]) b[f] = draft[f];
    if (draft.shipment_date !== base.shipment_date) b.shipment_date = draft.shipment_date;
    if (draft.comment !== base.comment) b.comment = draft.comment;
    if (linesDirty) {
      b.lines = draft.lines.map((l) => ({
        product_ref: l.product_ref,
        blocks: l.blocks,
        pieces: l.pieces,
        comment: l.comment,
      }));
    }
    return b;
  }, [draft, base, linesDirty, order.version]);
  const bodyKey = JSON.stringify(body);
  const debouncedKey = useDebounced(bodyKey, 400);
  // server-side recalculation (prices, stock, warnings, totals) of the unsaved state
  const preview = useQuery({
    queryKey: ['admin', 'orders', 'preview', order.public_id, debouncedKey],
    queryFn: async () =>
      classifyAdmin(await adminOrdersApi.preview(order.public_id, JSON.parse(debouncedKey))),
    // the debounced body lags behind: never preview the unchanged state
    enabled: editable && dirty && debouncedKey !== JSON.stringify({ version: order.version }),
    staleTime: 0,
    gcTime: 0,
  });
  const previewOut = preview.data;
  const previewOrder =
    dirty && debouncedKey === bodyKey && previewOut?.kind === 'ok' ? previewOut.order : null;
  const previewProblems = dirty && previewOut?.kind === 'invalid' ? previewOut.problems : [];

  const save = useMutation({
    mutationFn: () => adminOrdersApi.edit(order.public_id, body),
    onSuccess: (res) => void handle(res, t('aorders.saved')),
  });
  const reprice = useMutation({
    mutationFn: () =>
      adminOrdersApi.edit(order.public_id, { version: order.version, reprice: true }),
    onSuccess: (res) => void handle(res, t('aorders.saved')),
  });
  const status = useMutation({
    mutationFn: () =>
      adminOrdersApi.status(order.public_id, {
        status: statusValue,
        shipment_date: statusDate || null,
        version: order.version,
      }),
    onSuccess: (res) => {
      if (handle(res, t('aorders.statusChanged'))) setDialog(null);
    },
  });
  const resend = useMutation({
    mutationFn: () => adminOrdersApi.resend(order.public_id, order.version),
    onSuccess: (res) => void handle(res, t('aorders.resent')),
  });
  const remove = useMutation({
    mutationFn: () => adminOrdersApi.remove(order.public_id),
    onSuccess: (res) => {
      const ok = handle(res, t('aorders.deleted'));
      setDialog(null);
      // 202 returns the order in outcome_unknown: stay and show it
      if (ok && classifyAdmin(res).kind === 'ok' && (res.data as AdminOrder).state === 'deleted') {
        navigate('/admin/orders');
      }
    },
  });

  // ---- editing helpers ---------------------------------------------------------------------------
  const allowOver = order.limits?.allow_over_stock ?? false;
  const credit = (ref: string) =>
    order.state === 'sent' ? (order.lines.find((l) => l.product_ref === ref)?.quantity ?? 0) : 0;
  const recompute = (l: OrderLine): OrderLine => {
    const quantity = computeQty(l.blocks, l.pieces, l.pack_quantity);
    const warnings = l.warnings.filter((w) => w !== 'over_stock');
    if (!allowOver && l.stock != null && quantity > l.stock + credit(l.product_ref)) {
      warnings.push('over_stock');
    }
    return { ...l, quantity, line_sum: round2(quantity * (l.price ?? 0)), warnings };
  };
  const patchLine = (ref: string, patch: Partial<OrderLine>) =>
    setDraft((d) => ({
      ...d,
      lines: d.lines
        .map((l) => (l.product_ref === ref ? recompute({ ...l, ...patch }) : l))
        .filter((l) => l.blocks > 0 || l.pieces > 0),
    }));
  const addItems = (items: { item: CatalogItem; blocks: number; pieces: number }[]) =>
    setDraft((d) => {
      const lines = [...d.lines];
      for (const { item, blocks, pieces } of items) {
        const i = lines.findIndex((l) => l.product_ref === item.product_ref);
        const existing = lines[i];
        if (existing) {
          lines[i] = recompute({
            ...existing,
            blocks: existing.blocks + blocks,
            pieces: existing.pieces + pieces,
          });
        } else {
          lines.push(
            recompute(
              lineFromCatalog(
                { ...item, price: item.price ?? 0, stock: item.stock },
                blocks,
                pieces,
                lines.length + 1,
              ),
            ),
          );
        }
      }
      return { ...d, lines };
    });

  const refList = (f: HeaderField): RefItem[] => refs.named(REF_GROUP[f]);
  const source = useMemo<CatalogSource>(
    () => ({
      key: ['admin', draft.price_type, draft.warehouse],
      search: async (p, signal) => {
        const r = await productsApi.list(
          {
            q: p.q || undefined,
            category: p.category || undefined,
            price_type: draft.price_type,
            warehouse: draft.warehouse,
            active: true,
            hidden: false,
            limit: p.size,
            offset: (p.page - 1) * p.size,
          },
          signal,
        );
        return {
          total: r.total,
          items: r.items.map((x) => ({
            product_ref: x.sd_id,
            code: x.code,
            name: x.name,
            category: x.category,
            category_name: x.category_name,
            image_url: x.image_url,
            thumb_url: x.thumb_url,
            pack_quantity: x.pack_quantity,
            unit: null,
            price: x.price ?? null,
            stock: x.stock_qty ?? null,
            barcodes: x.barcodes.map((b) => b.barcode),
          })),
        };
      },
      categories: async () => refs.named('categories').map((c) => ({ id: c.id, name: c.name })),
    }),
    [draft.price_type, draft.warehouse, refs],
  );

  const { header: headerProblems, byLine } = splitProblems([...problems, ...previewProblems]);
  const drift = (previewOrder ?? order).price_drift ?? [];
  // lines as the server recalculated them (price / stock / warnings), edits kept from the draft
  const viewLines = previewOrder
    ? draft.lines.map((l) => {
        const p = previewOrder.lines.find((x) => x.product_ref === l.product_ref);
        return p
          ? {
              ...l,
              price: p.price,
              stock: p.stock,
              line_sum: p.line_sum,
              quantity: p.quantity,
              warnings: p.warnings,
            }
          : l;
      })
    : draft.lines;
  const busy =
    reprice.isPending || save.isPending || status.isPending || resend.isPending || remove.isPending;
  const canResend = canWrite && (order.state === 'failed' || order.state === 'outcome_unknown');
  const canStatus = canWrite && order.state === 'sent';
  const canDelete = canCancel && order.state !== 'deleted' && order.state !== 'submitting';

  const hasInvoice = !!order.sd_id && (order.state === 'sent' || order.state === 'cancelled');

  return (
    <>
      {hasInvoice && (
        <InvoiceDialog
          open={dialog === 'invoice'}
          onClose={() => setDialog(null)}
          queryKey={['admin', 'invoice', order.public_id, order.version]}
          load={() => adminOrdersApi.invoice(order.public_id)}
          pdfUrl={adminOrdersApi.invoicePdfUrl(
            order.public_id,
            i18n.language === 'uz' ? 'uz' : 'ru',
          )}
          fullScreen={mobile}
        />
      )}
      <PageHeader
        title={order.code}
        subtitle={`${order.client.name ?? order.client.sd_id} · ${order.link.name}`}
        actions={
          <>
            {hasInvoice && (
              <Button
                startIcon={<PrintIcon />}
                variant="outlined"
                onClick={() => setDialog('invoice')}
              >
                {t('invoice.open')}
              </Button>
            )}
            {canResend && (
              <Button
                startIcon={<ReplayIcon />}
                variant="outlined"
                onClick={() => resend.mutate()}
                disabled={busy}
              >
                {t('aorders.resend')}
              </Button>
            )}
            {canStatus && (
              <Button
                startIcon={<SwapHorizIcon />}
                variant="outlined"
                onClick={() => setDialog('status')}
                disabled={busy}
              >
                {t('aorders.changeStatus')}
              </Button>
            )}
            {canDelete && (
              <Button
                startIcon={<DeleteOutlineIcon />}
                color="error"
                variant="outlined"
                onClick={() => setDialog('delete')}
                disabled={busy}
              >
                {t('aorders.delete')}
              </Button>
            )}
            {editable && (
              <Button
                startIcon={<SaveIcon />}
                variant="contained"
                onClick={() => save.mutate()}
                disabled={!dirty || busy}
              >
                {order.state === 'sent' ? t('aorders.saveResend') : t('common.save')}
              </Button>
            )}
          </>
        }
      />

      <Stack spacing={2}>
        <Card variant="outlined">
          <CardContent>
            <Stack direction="row" spacing={2} useFlexGap flexWrap="wrap" alignItems="center">
              <StateChip state={order.state} sdStatus={order.sd_status} />
              {order.sd_id && <Typography>SD {order.sd_id}</Typography>}
              <Typography color="text.secondary">
                {t('orders.colCreated')}: {formatDateTime(order.created_at)}
              </Typography>
              {order.sent_at && (
                <Typography color="text.secondary">
                  {t('aorders.sentAt')}: {formatDateTime(order.sent_at)}
                </Typography>
              )}
              <Typography color="text.secondary">
                {t('aorders.createdBy')}: {order.created_by}
              </Typography>
            </Stack>
            {order.last_error?.message && (
              <Alert severity="error" sx={{ mt: 1.5 }}>
                {order.last_error.message}
              </Alert>
            )}
            {order.state === 'outcome_unknown' && order.last_error?.op && (
              <Alert severity="warning" sx={{ mt: 1.5 }} data-testid="awaiting">
                {t('aorders.awaiting', {
                  op: t(`aorders.op_${order.last_error.op.name}`, {
                    defaultValue: order.last_error.op.name,
                  }),
                })}
                {order.last_error.exhausted ? ` ${t('aorders.awaitingExhausted')}` : ''}
              </Alert>
            )}
            {!canWrite && (
              <Alert severity="info" sx={{ mt: 1.5 }}>
                {t('aorders.readOnly')}
              </Alert>
            )}
          </CardContent>
        </Card>

        <Box
          sx={{
            display: 'grid',
            gap: 2,
            gridTemplateColumns: { xs: '1fr', md: 'minmax(300px, 1fr) minmax(360px, 1.2fr)' },
          }}
        >
          <Card variant="outlined">
            <CardContent>
              <Stack spacing={2}>
                {HEADER_FIELDS.map((f) => {
                  const options = refList(f);
                  const cur = draft[f];
                  const known = options.some((o) => o.id === cur);
                  return (
                    <TextField
                      key={f}
                      select
                      label={t(`order.field_${f}`)}
                      value={cur ?? ''}
                      disabled={!editable || !refs.available}
                      onChange={(e) => setDraft((d) => ({ ...d, [f]: e.target.value || null }))}
                      slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}
                    >
                      <MenuItem value="">
                        <em>{t('order.choose')}</em>
                      </MenuItem>
                      {!known && cur && <MenuItem value={cur}>{order[f]?.name ?? cur}</MenuItem>}
                      {options.map((o) => (
                        <MenuItem key={o.id} value={o.id}>
                          {o.name ?? o.id}
                        </MenuItem>
                      ))}
                    </TextField>
                  );
                })}
                <TextField
                  type="date"
                  label={t('order.shipmentDate')}
                  value={draft.shipment_date ?? ''}
                  disabled={!editable}
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, shipment_date: e.target.value || null }))
                  }
                  slotProps={{ inputLabel: { shrink: true } }}
                />
                <TextField
                  label={t('order.comment')}
                  value={draft.comment}
                  disabled={!editable}
                  multiline
                  maxRows={4}
                  onChange={(e) => setDraft((d) => ({ ...d, comment: e.target.value }))}
                  slotProps={{ htmlInput: { maxLength: 500 } }}
                />
              </Stack>
            </CardContent>
          </Card>
          <Card variant="outlined">
            <CardContent sx={{ p: { xs: 1, sm: 2 } }}>
              <CategorySummary
                rows={(previewOrder ?? order).categories}
                totals={(previewOrder ?? order).totals}
              />
              {dirty && (
                <Typography sx={{ mt: 1 }} color="text.secondary" data-testid="local-total">
                  {t('aorders.localTotal')}:{' '}
                  <b>
                    {formatAmount(previewOrder?.totals.sum ?? local.sum)} {t('common.currency')}
                  </b>
                </Typography>
              )}
            </CardContent>
          </Card>
        </Box>

        {drift.length > 0 && (
          <Alert
            severity="warning"
            data-testid="price-drift"
            action={
              editable ? (
                <Button
                  color="inherit"
                  size="small"
                  disabled={busy || dirty}
                  onClick={() => reprice.mutate()}
                >
                  {t('aorders.reprice')}
                </Button>
              ) : undefined
            }
          >
            <AlertTitle>{t('aorders.driftTitle')}</AlertTitle>
            <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
              {drift.map((d) => (
                <li key={d.product_ref}>
                  {t('aorders.driftItem', {
                    name:
                      order.lines.find((l) => l.product_ref === d.product_ref)?.name ??
                      d.product_ref,
                    kept: formatAmount(d.kept_price),
                    current: formatAmount(d.current_price),
                  })}
                </li>
              ))}
            </Box>
          </Alert>
        )}

        {(headerProblems.length > 0 || (order.problems ?? []).length > 0) && editable && (
          <Alert severity={headerProblems.length > 0 ? 'error' : 'warning'} role="alert">
            <AlertTitle>{t('order.headerProblems')}</AlertTitle>
            <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
              {(headerProblems.length > 0
                ? headerProblems
                : (order.problems ?? []).filter((p) => !p.product_ref)
              ).map((p, i) => (
                <li key={`${p.code}-${i}`}>{problemText(t, p)}</li>
              ))}
            </Box>
          </Alert>
        )}

        <Paper variant="outlined">
          {editable && (
            <Box sx={{ p: 1.5 }}>
              <Button
                startIcon={<PlaylistAddIcon />}
                variant="outlined"
                onClick={() => setDialog('catalog')}
                disabled={!draft.price_type}
              >
                {t('order.addFromList')}
              </Button>
            </Box>
          )}
          <OrderLinesTable
            lines={viewLines}
            editable={editable}
            columns={defaultSettings().columns}
            filter=""
            problems={byLine}
            mobile={mobile}
            onQty={(ref, patch) => patchLine(ref, patch)}
            onComment={(ref, comment) => patchLine(ref, { comment })}
            onRemove={(ref) => patchLine(ref, { blocks: 0, pieces: 0 })}
          />
        </Paper>

        <Card variant="outlined">
          <CardContent>
            <Typography variant="h3" component="h2" sx={{ mb: 1.5 }}>
              {t('aorders.timeline')}
            </Typography>
            <OrderTimeline events={order.events} />
          </CardContent>
        </Card>
      </Stack>

      <AddFromCatalogDialog
        open={dialog === 'catalog'}
        source={source}
        fullScreen={mobile}
        onClose={() => setDialog(null)}
        onAdd={addItems}
      />

      <Dialog open={dialog === 'status'} onClose={() => setDialog(null)} fullWidth maxWidth="xs">
        <DialogTitle>{t('aorders.changeStatus')}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <TextField
              select
              label={t('aorders.newStatus')}
              value={statusValue}
              onChange={(e) => setStatusValue(Number(e.target.value))}
              helperText={statusValue === 5 && !canCancel ? t('aorders.needCancelPerm') : undefined}
            >
              {[1, 2, 3, 4, 5].map((s) => (
                <MenuItem key={s} value={s} disabled={s === 5 && !canCancel}>
                  {s}. {t(`sdStatus.s${s}`)}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              type="date"
              label={t('aorders.shipmentDateOpt')}
              value={statusDate}
              onChange={(e) => setStatusDate(e.target.value)}
              slotProps={{ inputLabel: { shrink: true } }}
            />
            {statusValue === 5 && order.sd_status === 2 && (
              <Alert severity="warning">{t('aorders.warnReturn')}</Alert>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialog(null)} disabled={status.isPending}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="contained"
            onClick={() => status.mutate()}
            disabled={
              status.isPending ||
              (statusValue === 5 && !canCancel) ||
              statusValue === order.sd_status
            }
          >
            {t('common.confirm')}
          </Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={dialog === 'delete'}
        destructive
        loading={remove.isPending}
        title={t('aorders.delete')}
        message={t('aorders.deleteConfirm', { code: order.code })}
        confirmLabel={t('aorders.delete')}
        onClose={() => setDialog(null)}
        onConfirm={() => remove.mutate()}
      />
    </>
  );
}

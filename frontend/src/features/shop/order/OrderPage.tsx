import CameraAltIcon from '@mui/icons-material/CameraAlt';
import PlaylistAddIcon from '@mui/icons-material/PlaylistAdd';
import SearchIcon from '@mui/icons-material/Search';
import SendIcon from '@mui/icons-material/Send';
import SettingsIcon from '@mui/icons-material/Settings';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import Alert from '@mui/material/Alert';
import AlertTitle from '@mui/material/AlertTitle';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import CircularProgress from '@mui/material/CircularProgress';
import IconButton from '@mui/material/IconButton';
import InputAdornment from '@mui/material/InputAdornment';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import useMediaQuery from '@mui/material/useMediaQuery';
import { useTheme } from '@mui/material/styles';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useSnackbar } from 'notistack';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link as RouterLink, useNavigate, useParams } from 'react-router-dom';
import { qk } from '@/api/keys';
import { catalogApi, ordersApi } from '@/api/shop';
import type { CatalogItem, Order, OrderProblem } from '@/api/types';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { ErrorState } from '@/components/States';
import { beepError, beepOk } from '@/lib/beep';
import { describeError } from '@/lib/errors';
import { formatMoney } from '@/lib/format';
import { AddFromCatalogDialog, type CatalogSource } from '@/features/orders/AddFromCatalogDialog';
import { OrderLinesTable } from '@/features/orders/OrderLinesTable';
import { StateChip } from '@/features/orders/StateChip';
import {
  canShopCancel,
  orderErrorMessage,
  problemText,
  splitProblems,
} from '@/features/orders/orderUtils';
import { useOrderSettings } from '@/features/orders/useOrderSettings';
import { ShopShell } from '../ShopShell';
import { useShopMe } from '../queries';
import { AmbiguousDialog } from './AmbiguousDialog';
import { CameraScanDialog } from './CameraScanDialog';
import { ImportDialog } from './ImportDialog';
import { OrderFormGrid } from './OrderFormGrid';
import { OrderHeaderCard } from './OrderHeaderCard';
import { ProcessingPanel, SentPanel } from './OrderPanels';
import { ScannerInput } from './ScannerInput';
import { SettingsDialog } from './SettingsDialog';
import { classifySubmit } from './submit';
import { useShopOrder, type SaveState } from './useShopOrder';

/** catalog picks above this size are saved with one bulk request (import apply) */
const BULK_ADD_FROM = 10;
const BUSY_RETRIES = 5;
const BUSY_DELAY_MS = 2000;
const lastPriceKey = (slug: string) => `autoshop.lastPriceType.${slug}`;

function readLastPrice(slug: string): string | null {
  try {
    return localStorage.getItem(lastPriceKey(slug));
  } catch {
    return null;
  }
}

function SaveIndicator({ state }: { state: SaveState }) {
  const { t } = useTranslation();
  return (
    <Chip
      size="small"
      variant="outlined"
      color={state === 'error' ? 'error' : state === 'saved' ? 'success' : 'default'}
      icon={state === 'saving' ? <CircularProgress size={12} /> : undefined}
      label={t(`order.save_${state}`)}
      role="status"
      data-testid="save-indicator"
      data-state={state}
    />
  );
}

export default function OrderPage() {
  const { t } = useTranslation();
  const { slug = '', id = '' } = useParams();
  const me = useShopMe().data;
  const editor = useShopOrder(id);
  const { order } = editor;

  return (
    <ShopShell wide title={me?.link.title} logoUrl={me?.link.logo_url} color={me?.link.color}>
      {editor.query.isPending && (
        <Box sx={{ display: 'grid', placeItems: 'center', py: 8 }}>
          <CircularProgress aria-label={t('common.loading')} />
        </Box>
      )}
      {editor.query.isError && !order && (
        <ErrorState error={editor.query.error} onRetry={() => void editor.query.refetch()} />
      )}
      {order && me && (
        <OrderScreen key={order.public_id} slug={slug} me={me} editor={editor} order={order} />
      )}
    </ShopShell>
  );
}

type Editor = ReturnType<typeof useShopOrder>;

function OrderScreen({
  slug,
  me,
  editor,
  order,
}: {
  slug: string;
  me: NonNullable<ReturnType<typeof useShopMe>['data']>;
  editor: Editor;
  order: Order;
}) {
  const { t } = useTranslation();
  const theme = useTheme();
  const mobile = useMediaQuery(theme.breakpoints.down('sm'));
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  const [settings, setSettings] = useOrderSettings();

  const editable = order.editable;
  const [filter, setFilter] = useState('');
  const [highlight, setHighlight] = useState<string | null>(null);
  const [dialog, setDialog] = useState<
    'catalog' | 'import' | 'camera' | 'settings' | 'confirm' | 'cancel' | null
  >(null);
  const [ambiguous, setAmbiguous] = useState<{ code: string; candidates: CatalogItem[] } | null>(
    null,
  );
  const [submitting, setSubmitting] = useState(false);
  const [justSent, setJustSent] = useState(false);
  const [submitProblems, setSubmitProblems] = useState<OrderProblem[] | null>(null);
  const [submitNote, setSubmitNote] = useState<{
    kind: 'unavailable' | 'busy' | 'rejected';
    message?: string;
  } | null>(null);
  const unmounted = useRef(false);
  useEffect(() => {
    unmounted.current = false;
    return () => {
      unmounted.current = true;
    };
  }, []);

  const needsPriceType = editable && !order.price_type;
  const toolsDisabled = !editable || needsPriceType;
  const dialogOpen = dialog !== null || ambiguous !== null;

  const problems = useMemo<OrderProblem[]>(() => {
    if (submitProblems) return submitProblems;
    if (order.state === 'failed') return order.last_error?.errors ?? [];
    return [];
  }, [submitProblems, order.state, order.last_error]);
  const { header: headerProblems, byLine } = useMemo(() => splitProblems(problems), [problems]);
  const liveLimits = (order.problems ?? []).filter((p) =>
    ['min_order_sum', 'max_order_sum', 'client_inactive'].includes(p.code),
  );

  // ---- scanning -----------------------------------------------------------------------------
  const feedback = useCallback(
    (kind: 'ok' | 'error') => {
      if (!settings.sound) return;
      if (kind === 'ok') beepOk();
      else beepError();
    },
    [settings.sound],
  );

  const doScan = useCallback(
    async (code: string, productRef?: string) => {
      try {
        const r = await editor.scan(code, productRef);
        if (r.kind === 'ok') {
          feedback('ok');
          setHighlight(r.ref);
        } else if (r.kind === 'ambiguous') {
          setAmbiguous({ code, candidates: r.candidates });
        } else {
          feedback('error');
          enqueueSnackbar(t(`order.scan_${r.reason}`, { code }), { variant: 'error' });
        }
      } catch (e) {
        feedback('error');
        enqueueSnackbar(orderErrorMessage(e, t), { variant: 'error' });
      }
    },
    [editor, feedback, enqueueSnackbar, t],
  );

  // ---- catalog (price type / warehouse of this order) -------------------------------------------
  const priceType = order.price_type?.id ?? null;
  const warehouse = order.warehouse?.id ?? null;
  const source = useMemo<CatalogSource>(
    () => ({
      key: ['shop', priceType, warehouse],
      search: async (p, signal) => {
        void signal;
        const r = await catalogApi.search({
          q: p.q || undefined,
          category: p.category || undefined,
          price_type: priceType ?? undefined,
          warehouse: warehouse ?? undefined,
          page: p.page,
          size: p.size,
        });
        return { items: r.items, total: r.total };
      },
      categories: async () => (await catalogApi.categories(priceType ?? undefined)).items,
    }),
    [priceType, warehouse],
  );

  // ---- header ------------------------------------------------------------------------------------
  const onHeader = (patch: Parameters<Editor['setHeader']>[0]) => editor.setHeader(patch);

  // ---- submit / cancel / repeat ----------------------------------------------------------------------
  const rememberPriceType = (o: Order) => {
    try {
      if (o.price_type) localStorage.setItem(lastPriceKey(slug), o.price_type.id);
    } catch {
      /* ignore */
    }
  };

  const submit = useCallback(async () => {
    setSubmitting(true);
    setSubmitNote(null);
    try {
      await editor.flush();
      let res = await ordersApi.submit(order.public_id, editor.versionRef.current);
      let out = classifySubmit(res);
      // another operation holds the order: wait and retry a few times
      for (let tries = 0; out.kind === 'busy' && tries < BUSY_RETRIES; tries++) {
        setSubmitNote({ kind: 'busy' });
        await new Promise((r) => setTimeout(r, BUSY_DELAY_MS));
        if (unmounted.current) return;
        res = await ordersApi.submit(order.public_id, editor.versionRef.current);
        out = classifySubmit(res);
      }
      setSubmitNote(null);
      switch (out.kind) {
        case 'sent':
          editor.apply(out.order);
          setSubmitProblems(null);
          rememberPriceType(out.order);
          setJustSent(true);
          break;
        case 'unknown':
          editor.apply(out.order);
          setSubmitProblems(null);
          break;
        case 'invalid':
          setSubmitProblems(out.problems);
          if (out.rejected) setSubmitNote({ kind: 'rejected', message: out.message });
          await editor.refetch().catch(() => undefined);
          break;
        case 'unavailable':
          setSubmitNote({ kind: 'unavailable', message: out.message });
          await editor.refetch().catch(() => undefined);
          break;
        case 'busy':
          enqueueSnackbar(t('orderErrors.order_busy'), { variant: 'warning' });
          break;
        case 'conflict':
          enqueueSnackbar(t('orderErrors.version_conflict'), { variant: 'warning' });
          await editor.refetch().catch(() => undefined);
          break;
        case 'invalid_state':
          enqueueSnackbar(t('orderErrors.invalid_state'), { variant: 'warning' });
          await editor.refetch().catch(() => undefined);
          break;
        default:
          enqueueSnackbar(res.status === 429 ? t('errors.tooManyNoTime') : t('errors.server'), {
            variant: 'error',
          });
      }
    } catch (e) {
      enqueueSnackbar(describeError(e, t), { variant: 'error' });
    } finally {
      if (!unmounted.current) setSubmitting(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor, order.public_id, enqueueSnackbar, t]);

  const startNew = () => navigate(`/s/${slug}/order/new`);

  const repeat = useMutation({
    mutationFn: () => ordersApi.duplicate(order.public_id),
    onSuccess: (o) => navigate(`/s/${slug}/orders/${o.public_id}`),
  });
  const cancel = useMutation({
    mutationFn: async () => {
      const res = await ordersApi.cancel(order.public_id, order.version);
      if (!res.ok) {
        const d = (res.data as { detail?: { code?: string; message?: string } } | null)?.detail;
        throw new Error(d?.code ?? `HTTP ${res.status}`);
      }
      return res.data as Order;
    },
    meta: { silent: true },
    onSuccess: (o) => {
      editor.apply(o);
      setDialog(null);
      // 202: SD has not confirmed yet; the order is outcome_unknown and the screen polls it
      enqueueSnackbar(
        o.state === 'outcome_unknown' ? t('orders.pendingConfirm') : t('orders.cancelled'),
        { variant: o.state === 'outcome_unknown' ? 'info' : 'success' },
      );
      void qc.invalidateQueries({ queryKey: qk.shop.orders.all() });
    },
    onError: (e: Error) => {
      setDialog(null);
      enqueueSnackbar(t(`orderErrors.${e.message}`, { defaultValue: t('errors.server') }), {
        variant: 'error',
      });
      void editor.refetch();
    },
  });

  // ---- render ---------------------------------------------------------------------------------------
  if (justSent && order.state === 'sent') {
    return (
      <SentPanel
        order={order}
        slug={slug}
        onNew={startNew}
        onRepeat={() => repeat.mutate()}
        busy={repeat.isPending}
      />
    );
  }
  if (order.state === 'outcome_unknown' || order.state === 'submitting') {
    return <ProcessingPanel order={order} slug={slug} />;
  }

  const totals = order.totals;
  const currency = t('common.currency');
  const canSubmit = editable && editor.lines.length > 0 && !submitting;
  const submitButton = (
    <Button
      variant="contained"
      size="large"
      startIcon={submitting ? <CircularProgress size={18} color="inherit" /> : <SendIcon />}
      disabled={!canSubmit}
      onClick={() => setDialog('confirm')}
      sx={{ minHeight: 48, whiteSpace: 'nowrap' }}
    >
      {t('order.submit')}
    </Button>
  );

  return (
    <Stack spacing={2} sx={{ pb: mobile && editable ? 12 : 0 }}>
      {!editable && (
        <Alert
          severity={order.state === 'sent' ? 'success' : 'info'}
          action={
            <Stack direction="row" spacing={1}>
              {canShopCancel(order) && (
                <Button color="inherit" size="small" onClick={() => setDialog('cancel')}>
                  {t('orders.cancel')}
                </Button>
              )}
              <Button
                color="inherit"
                size="small"
                onClick={() => repeat.mutate()}
                disabled={repeat.isPending}
              >
                {t('orders.repeat')}
              </Button>
            </Stack>
          }
        >
          <Stack direction="row" spacing={1} alignItems="center" useFlexGap flexWrap="wrap">
            <Typography component="span" fontWeight={600}>
              {t('order.code')}: {order.code}
              {order.sd_id ? ` · SD ${order.sd_id}` : ''}
            </Typography>
            <StateChip state={order.state} sdStatus={order.sd_status} />
          </Stack>
        </Alert>
      )}

      {order.state === 'failed' && (
        <Alert severity="error" role="alert">
          <AlertTitle>{t('order.failedTitle')}</AlertTitle>
          {order.last_error?.message ?? t('order.failedText')}
        </Alert>
      )}

      <OrderHeaderCard me={me} order={order} slug={slug} summaryTotals={totals} />

      <Card variant="outlined">
        <CardContent>
          <Stack spacing={2}>
            <OrderFormGrid
              order={order}
              options={me.options}
              editable={editable}
              pending={editor.headerValue}
              onChange={onHeader}
              lastPriceType={me.last_price_type_sd_id ?? readLastPrice(slug)}
            />
            <TextField
              label={t('order.comment')}
              value={editor.headerValue('comment') ?? order.comment}
              disabled={!editable}
              onChange={(e) => onHeader({ comment: e.target.value })}
              multiline
              minRows={1}
              maxRows={4}
              fullWidth
              slotProps={{ htmlInput: { maxLength: 500 } }}
            />
          </Stack>
        </CardContent>
      </Card>

      {editable && (
        <Paper variant="outlined" sx={{ p: { xs: 1.5, sm: 2 } }}>
          <Stack spacing={1.5}>
            {needsPriceType && <Alert severity="info">{t('order.pickPriceTypeFirst')}</Alert>}
            <Stack
              direction={{ xs: 'column', md: 'row' }}
              spacing={1.5}
              alignItems={{ md: 'center' }}
            >
              <Box sx={{ flex: { md: '1 1 420px' }, order: { xs: -1, md: 0 } }}>
                <Stack direction="row" spacing={1} alignItems="center">
                  <ScannerInput
                    onScan={(code) => doScan(code)}
                    alwaysFocus={settings.autoFocus}
                    paused={dialogOpen}
                    big={mobile}
                    disabled={toolsDisabled}
                  />
                  {mobile && (
                    <IconButton
                      color="primary"
                      size="large"
                      aria-label={t('order.camera')}
                      onClick={() => setDialog('camera')}
                      disabled={toolsDisabled}
                    >
                      <CameraAltIcon />
                    </IconButton>
                  )}
                </Stack>
              </Box>
              <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
                <Button
                  variant="outlined"
                  startIcon={<PlaylistAddIcon />}
                  onClick={() => setDialog('catalog')}
                  disabled={toolsDisabled}
                >
                  {t('order.addFromList')}
                </Button>
                <Button
                  variant="outlined"
                  startIcon={<UploadFileIcon />}
                  onClick={() => setDialog('import')}
                  disabled={toolsDisabled}
                >
                  {t('order.importFromFile')}
                </Button>
              </Stack>
              <TextField
                size="small"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder={t('order.tableSearch')}
                label={t('order.tableSearch')}
                sx={{ minWidth: { md: 200 } }}
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
              />
              <Stack direction="row" spacing={1} alignItems="center" sx={{ ml: { md: 'auto' } }}>
                <SaveIndicator state={editor.saveState} />
                <Tooltip title={t('order.settings')}>
                  <IconButton
                    aria-label={t('order.settings')}
                    onClick={() => setDialog('settings')}
                  >
                    <SettingsIcon />
                  </IconButton>
                </Tooltip>
              </Stack>
            </Stack>
          </Stack>
        </Paper>
      )}

      {(headerProblems.length > 0 || submitNote || liveLimits.length > 0) && (
        <Stack spacing={1}>
          {submitNote?.kind === 'unavailable' && (
            <Alert
              severity="error"
              action={
                <Button color="inherit" size="small" onClick={() => void submit()}>
                  {t('common.retry')}
                </Button>
              }
            >
              {t('order.unavailable')}
            </Alert>
          )}
          {submitNote?.kind === 'busy' && <Alert severity="info">{t('order.busy')}</Alert>}
          {submitNote?.kind === 'rejected' && (
            <Alert severity="error">
              <AlertTitle>{t('order.rejected')}</AlertTitle>
              {submitNote.message}
            </Alert>
          )}
          {headerProblems.length > 0 && (
            <Alert severity="error" role="alert" data-testid="header-problems">
              <AlertTitle>{t('order.headerProblems')}</AlertTitle>
              <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
                {headerProblems.map((p, i) => (
                  <li key={`${p.code}-${i}`}>{problemText(t, p)}</li>
                ))}
              </Box>
            </Alert>
          )}
          {liveLimits.map((p) => (
            <Alert key={p.code} severity="warning">
              {problemText(t, p)}
            </Alert>
          ))}
        </Stack>
      )}

      <Paper variant="outlined" sx={{ p: mobile ? 1 : 0 }}>
        <OrderLinesTable
          lines={editor.lines}
          editable={editable}
          columns={settings.columns}
          filter={filter}
          problems={byLine}
          mobile={mobile}
          highlightRef={highlight}
          onQty={(ref, patch) => editor.setQty(ref, patch)}
          onComment={editor.setComment}
          onRemove={editor.removeLine}
        />
      </Paper>

      {editable && !mobile && (
        // sticky: the total and the submit button stay visible while scrolling long orders
        <Paper
          variant="outlined"
          data-testid="total-bar"
          sx={{
            p: 2,
            position: 'sticky',
            bottom: 0,
            zIndex: (th) => th.zIndex.appBar - 1,
            boxShadow: (th) => th.shadows[6],
          }}
        >
          <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={2}>
            <Box>
              <Typography color="text.secondary">{t('order.total')}</Typography>
              <Typography variant="h2" component="p" data-testid="grand-total">
                {formatMoney(totals.sum, currency)}
              </Typography>
            </Box>
            {submitButton}
          </Stack>
        </Paper>
      )}

      {editable && mobile && (
        <Paper
          elevation={8}
          square
          data-testid="sticky-bar"
          sx={{
            position: 'fixed',
            left: 0,
            right: 0,
            bottom: 0,
            zIndex: (th) => th.zIndex.appBar,
            p: 1.5,
          }}
        >
          <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1.5}>
            <Box>
              <Typography variant="caption" color="text.secondary">
                {t('order.total')}
              </Typography>
              <Typography fontWeight={700} data-testid="grand-total">
                {formatMoney(totals.sum, currency)}
              </Typography>
            </Box>
            {submitButton}
          </Stack>
        </Paper>
      )}

      <AddFromCatalogDialog
        open={dialog === 'catalog'}
        source={source}
        fullScreen={mobile}
        onClose={() => setDialog(null)}
        onAdd={(items) =>
          // many products at once ("select all"): one bulk request instead of one per line
          items.length >= BULK_ADD_FROM
            ? void editor
                .importApply(
                  items.map(({ item, blocks, pieces }) => ({
                    product_ref: item.product_ref,
                    blocks,
                    pieces,
                  })),
                )
                .catch((e: unknown) =>
                  enqueueSnackbar(orderErrorMessage(e, t), { variant: 'error' }),
                )
            : editor.addItems(items)
        }
      />
      <ImportDialog
        open={dialog === 'import'}
        orderId={order.public_id}
        fullScreen={mobile}
        onApply={(lines) => editor.importApply(lines)}
        onClose={() => setDialog(null)}
      />
      <CameraScanDialog
        open={dialog === 'camera'}
        onClose={() => setDialog(null)}
        onDetect={(code) => void doScan(code)}
      />
      <SettingsDialog
        open={dialog === 'settings'}
        settings={settings}
        onChange={setSettings}
        onClose={() => setDialog(null)}
      />
      <AmbiguousDialog
        code={ambiguous?.code ?? null}
        candidates={ambiguous?.candidates ?? []}
        onClose={() => setAmbiguous(null)}
        onPick={(item) => {
          const code = ambiguous?.code ?? '';
          setAmbiguous(null);
          void doScan(code, item.product_ref);
        }}
      />
      <ConfirmDialog
        open={dialog === 'confirm'}
        title={t('order.confirmTitle')}
        confirmLabel={t('order.confirmSend')}
        message={t('order.confirmText', {
          lines: totals.lines_count,
          qty: totals.qty,
          sum: formatMoney(totals.sum, currency),
        })}
        onClose={() => setDialog(null)}
        onConfirm={() => {
          setDialog(null);
          void submit();
        }}
      />
      <ConfirmDialog
        open={dialog === 'cancel'}
        destructive
        loading={cancel.isPending}
        title={t('orders.cancelTitle')}
        message={t('orders.cancelConfirm', { code: order.code })}
        confirmLabel={t('orders.cancel')}
        onClose={() => setDialog(null)}
        onConfirm={() => cancel.mutate()}
      />
      {order.state === 'cancelled' && (
        <Button component={RouterLink} to={`/s/${slug}/orders`}>
          {t('orders.toList')}
        </Button>
      )}
    </Stack>
  );
}

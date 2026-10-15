import SearchIcon from '@mui/icons-material/Search';
import VisibilityIcon from '@mui/icons-material/Visibility';
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardActionArea from '@mui/material/CardActionArea';
import Checkbox from '@mui/material/Checkbox';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import InputAdornment from '@mui/material/InputAdornment';
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
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSnackbar } from 'notistack';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { productsApi } from '@/api/admin';
import { qk } from '@/api/keys';
import type { AdminProduct } from '@/api/types';
import { PageHeader } from '@/components/PageHeader';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/States';
import { ViewToggle } from '@/components/ViewToggle';
import { ProductThumb } from '@/features/orders/ProductThumb';
import { useDebounced } from '@/hooks/useDebounced';
import { usePaging } from '@/hooks/usePaging';
import { useViewMode } from '@/hooks/useViewMode';
import { formatAmount } from '@/lib/format';
import { useCan } from '../AdminContext';
import { useRefs } from '../queries';
import { ProductDrawer } from './ProductDrawer';

type Tri = 'all' | 'yes' | 'no';
const tri = (v: Tri): boolean | undefined => (v === 'all' ? undefined : v === 'yes');

function TriSelect({
  label,
  value,
  onChange,
}: {
  label: string;
  value: Tri;
  onChange: (v: Tri) => void;
}) {
  const { t } = useTranslation();
  return (
    <TextField
      select
      label={label}
      value={value}
      onChange={(e) => onChange(e.target.value as Tri)}
      fullWidth={false}
      sx={{ flex: '1 1 150px' }}
    >
      <MenuItem value="all">{t('common.all')}</MenuItem>
      <MenuItem value="yes">{t('common.yes')}</MenuItem>
      <MenuItem value="no">{t('common.no')}</MenuItem>
    </TextField>
  );
}

const stockTotal = (p: AdminProduct) => p.stock.reduce((a, s) => a + s.quantity, 0);

function StatusChips({ p }: { p: AdminProduct }) {
  const { t } = useTranslation();
  return (
    <Stack direction="row" spacing={0.5} useFlexGap flexWrap="wrap">
      <Chip
        size="small"
        color={p.active ? 'success' : 'default'}
        label={p.active ? t('prod.active') : t('prod.inactive')}
      />
      {p.hidden && <Chip size="small" color="warning" label={t('prod.hidden')} />}
    </Stack>
  );
}

export default function AdminProductsPage() {
  const { t } = useTranslation();
  const can = useCan();
  const canWrite = can('products.write');
  const refs = useRefs();
  const qc = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  const paging = usePaging(25);
  const [view, setView] = useViewMode('admin-products-view');
  const [search, setSearch] = useState('');
  const [active, setActive] = useState<Tri>('all');
  const [hidden, setHidden] = useState<Tri>('all');
  const [hasBarcode, setHasBarcode] = useState<Tri>('all');
  const [category, setCategory] = useState('');
  const [selected, setSelected] = useState<AdminProduct | null>(null);
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  /** the selection came from "select all matching the filter" */
  const [allMatching, setAllMatching] = useState(false);
  const [confirm, setConfirm] = useState<boolean | null>(null); // the `hidden` value to apply
  const q = useDebounced(search.trim());

  const filters = {
    q: q || undefined,
    active: tri(active),
    hidden: tri(hidden),
    has_barcode: tri(hasBarcode),
    category: category || undefined,
  };
  const params = { ...filters, limit: paging.pageSize, offset: paging.offset };
  const list = useQuery({
    queryKey: qk.admin.products.list(params),
    queryFn: ({ signal }) => productsApi.list(params, signal),
    placeholderData: keepPreviousData,
  });
  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;

  const clearSelection = () => {
    setPicked(new Set());
    setAllMatching(false);
  };
  // a filter change makes an earlier selection meaningless
  const reset =
    <T,>(set: (v: T) => void) =>
    (v: T) => {
      set(v);
      paging.reset();
      clearSelection();
    };

  const toggle = (sdId: string) => {
    setAllMatching(false);
    setPicked((cur) => {
      const next = new Set(cur);
      if (next.has(sdId)) next.delete(sdId);
      else next.add(sdId);
      return next;
    });
  };
  const pageAll = items.length > 0 && items.every((p) => picked.has(p.sd_id));
  const pageSome = items.some((p) => picked.has(p.sd_id));
  const togglePage = () => {
    setAllMatching(false);
    setPicked((cur) => {
      const next = new Set(cur);
      for (const p of items) {
        if (pageAll) next.delete(p.sd_id);
        else next.add(p.sd_id);
      }
      return next;
    });
  };

  const matching = useMutation({
    mutationFn: () => productsApi.ids(filters),
    onSuccess: (r) => {
      setPicked(new Set(r.sd_ids));
      setAllMatching(true);
    },
  });
  const bulk = useMutation({
    mutationFn: (hide: boolean) => productsApi.bulkHidden([...picked], hide),
    onSuccess: (r) => {
      enqueueSnackbar(t('prod.bulkDone', { count: r.updated }), { variant: 'success' });
      clearSelection();
      void qc.invalidateQueries({ queryKey: qk.admin.products.all() });
    },
    onSettled: () => setConfirm(null),
  });

  const pageCheckbox = (
    <Checkbox
      checked={pageAll}
      indeterminate={!pageAll && pageSome}
      disabled={items.length === 0}
      onChange={togglePage}
      slotProps={{ input: { 'aria-label': t('prod.selectPage') } }}
    />
  );
  const rowCheckbox = (p: AdminProduct, sx?: object) => (
    <Checkbox
      checked={picked.has(p.sd_id)}
      onChange={() => toggle(p.sd_id)}
      onClick={(e) => e.stopPropagation()}
      slotProps={{ input: { 'aria-label': t('prod.select', { name: p.name }) } }}
      sx={sx}
    />
  );

  return (
    <>
      <PageHeader title={t('nav.products')} />
      <Paper variant="outlined">
        <Stack
          direction="row"
          spacing={1.5}
          useFlexGap
          flexWrap="wrap"
          alignItems="center"
          sx={{ p: 2 }}
        >
          <TextField
            label={t('common.search')}
            placeholder={t('prod.searchHint')}
            value={search}
            onChange={(e) => reset(setSearch)(e.target.value)}
            fullWidth={false}
            sx={{ flex: '3 1 260px' }}
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
          <TriSelect label={t('prod.fActive')} value={active} onChange={reset(setActive)} />
          <TriSelect label={t('prod.fHidden')} value={hidden} onChange={reset(setHidden)} />
          <TriSelect
            label={t('prod.fHasBarcode')}
            value={hasBarcode}
            onChange={reset(setHasBarcode)}
          />
          <TextField
            select
            label={t('order.colCategory')}
            value={category}
            onChange={(e) => reset(setCategory)(e.target.value)}
            fullWidth={false}
            sx={{ flex: '2 1 200px' }}
          >
            <MenuItem value="">{t('common.all')}</MenuItem>
            {refs.named('categories').map((c) => (
              <MenuItem key={c.id} value={c.id}>
                {c.name ?? c.id}
              </MenuItem>
            ))}
          </TextField>
          <ViewToggle value={view} onChange={setView} />
        </Stack>

        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          useFlexGap
          flexWrap="wrap"
          sx={{ px: 2, pb: 1, minHeight: 44 }}
          data-testid="bulk-bar"
        >
          {view === 'grid' && pageCheckbox}
          {view === 'grid' && <Typography variant="body2">{t('prod.selectPage')}</Typography>}
          {picked.size > 0 && (
            <>
              <Typography variant="body2" fontWeight={600}>
                {t('prod.selected', { count: picked.size })}
              </Typography>
              {canWrite && (
                <>
                  <Button
                    size="small"
                    variant="outlined"
                    startIcon={<VisibilityOffIcon />}
                    onClick={() => setConfirm(true)}
                  >
                    {t('prod.bulkHide')}
                  </Button>
                  <Button
                    size="small"
                    variant="outlined"
                    startIcon={<VisibilityIcon />}
                    onClick={() => setConfirm(false)}
                  >
                    {t('prod.bulkShow')}
                  </Button>
                </>
              )}
              <Button size="small" onClick={clearSelection}>
                {t('prod.clearSelection')}
              </Button>
            </>
          )}
        </Stack>
        {pageAll && total > items.length && (
          <Alert severity="info" icon={false} sx={{ mx: 2, mb: 1, py: 0 }}>
            {allMatching ? (
              <>
                {t('prod.allSelected', { count: picked.size })}{' '}
                <Button size="small" onClick={clearSelection}>
                  {t('prod.clearSelection')}
                </Button>
              </>
            ) : (
              <>
                {t('prod.pageSelected', { count: items.length })}{' '}
                <Button
                  size="small"
                  disabled={matching.isPending}
                  onClick={() => matching.mutate()}
                >
                  {t('prod.selectAllMatching', { count: total })}
                </Button>
              </>
            )}
          </Alert>
        )}

        {view === 'list' ? (
          <TableContainer>
            <Table size="small" aria-label={t('nav.products')}>
              <TableHead>
                <TableRow>
                  <TableCell padding="checkbox">{pageCheckbox}</TableCell>
                  <TableCell>{t('order.colPhoto')}</TableCell>
                  <TableCell>{t('order.colProduct')}</TableCell>
                  <TableCell>{t('order.colCategory')}</TableCell>
                  <TableCell>{t('common.status')}</TableCell>
                  <TableCell align="right">{t('prod.barcodes')}</TableCell>
                  <TableCell align="right">{t('order.colStock')}</TableCell>
                </TableRow>
              </TableHead>
              {list.isPending ? (
                <TableSkeleton cols={7} />
              ) : (
                <TableBody>
                  {items.map((p) => (
                    <TableRow
                      key={p.sd_id}
                      hover
                      selected={picked.has(p.sd_id)}
                      tabIndex={0}
                      role="button"
                      aria-label={`${t('prod.open')}: ${p.name}`}
                      onClick={() => setSelected(p)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') setSelected(p);
                      }}
                      sx={{ cursor: 'pointer' }}
                    >
                      <TableCell padding="checkbox">{rowCheckbox(p)}</TableCell>
                      <TableCell onClick={(e) => e.stopPropagation()}>
                        <ProductThumb name={p.name} thumbUrl={p.thumb_url} imageUrl={p.image_url} />
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2" fontWeight={600}>
                          {p.name}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {p.code ?? p.sd_id}
                        </Typography>
                      </TableCell>
                      <TableCell>{p.category_name ?? '—'}</TableCell>
                      <TableCell>
                        <StatusChips p={p} />
                      </TableCell>
                      <TableCell align="right">{p.barcodes.length}</TableCell>
                      <TableCell align="right">{formatAmount(stockTotal(p))}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              )}
            </Table>
          </TableContainer>
        ) : (
          <Box
            component="ul"
            aria-label={t('nav.products')}
            sx={{
              listStyle: 'none',
              m: 0,
              px: 2,
              py: 1,
              display: 'grid',
              gap: 1.5,
              gridTemplateColumns: {
                xs: 'repeat(2, minmax(0, 1fr))',
                sm: 'repeat(3, minmax(0, 1fr))',
                md: 'repeat(4, minmax(0, 1fr))',
                lg: 'repeat(6, minmax(0, 1fr))',
              },
            }}
          >
            {items.map((p) => {
              const on = picked.has(p.sd_id);
              return (
                <Card
                  component="li"
                  key={p.sd_id}
                  variant="outlined"
                  sx={{
                    position: 'relative',
                    borderColor: on ? 'primary.main' : 'divider',
                    bgcolor: on ? 'action.selected' : 'background.paper',
                  }}
                >
                  {rowCheckbox(p, {
                    position: 'absolute',
                    top: 10,
                    left: 10,
                    zIndex: 1,
                    p: 0.25,
                    bgcolor: 'background.paper',
                    borderRadius: 1,
                    '&:hover': { bgcolor: 'background.paper' },
                  })}
                  <Box sx={{ p: 1, pb: 0 }}>
                    <ProductThumb
                      fill
                      name={p.name}
                      thumbUrl={p.thumb_url}
                      imageUrl={p.image_url}
                    />
                  </Box>
                  <CardActionArea
                    onClick={() => setSelected(p)}
                    aria-label={`${t('prod.open')}: ${p.name}`}
                    sx={{ p: 1, display: 'flex', flexDirection: 'column', alignItems: 'stretch' }}
                  >
                    <Typography
                      variant="body2"
                      fontWeight={600}
                      title={p.name ?? undefined}
                      sx={{
                        display: '-webkit-box',
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: 'vertical',
                        overflow: 'hidden',
                        minHeight: '2.86em',
                        lineHeight: 1.43,
                      }}
                    >
                      {p.name}
                    </Typography>
                    <Typography variant="caption" color="text.secondary" noWrap>
                      {p.code ?? p.sd_id} · {p.category_name ?? '—'}
                    </Typography>
                    <Stack
                      direction="row"
                      justifyContent="space-between"
                      alignItems="center"
                      sx={{ mt: 0.75 }}
                    >
                      <StatusChips p={p} />
                      <Typography variant="caption" color="text.secondary" sx={{ flexShrink: 0 }}>
                        {t('prod.stockTotal')}: {formatAmount(stockTotal(p))}
                      </Typography>
                    </Stack>
                  </CardActionArea>
                </Card>
              );
            })}
          </Box>
        )}
        {list.isError && <ErrorState error={list.error} onRetry={() => void list.refetch()} />}
        {list.isSuccess && items.length === 0 && <EmptyState title={t('prod.empty')} />}
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
      </Paper>
      <ProductDrawer
        product={selected}
        canWrite={canWrite}
        onClose={() => setSelected(null)}
        onChange={setSelected}
      />
      <Dialog open={confirm !== null} onClose={() => setConfirm(null)} maxWidth="xs" fullWidth>
        <DialogContent>
          <Typography>
            {t(confirm ? 'prod.bulkConfirmHide' : 'prod.bulkConfirmShow', {
              count: picked.size,
            })}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirm(null)}>{t('common.cancel')}</Button>
          <Button
            variant="contained"
            disabled={bulk.isPending}
            onClick={() => confirm !== null && bulk.mutate(confirm)}
          >
            {t('common.confirm')}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

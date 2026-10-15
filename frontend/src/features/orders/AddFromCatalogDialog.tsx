import GridViewIcon from '@mui/icons-material/GridView';
import SearchIcon from '@mui/icons-material/Search';
import ViewListIcon from '@mui/icons-material/ViewList';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import InputAdornment from '@mui/material/InputAdornment';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableContainer from '@mui/material/TableContainer';
import TableHead from '@mui/material/TableHead';
import TablePagination from '@mui/material/TablePagination';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import ToggleButton from '@mui/material/ToggleButton';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';
import Typography from '@mui/material/Typography';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { CatalogItem } from '@/api/types';
import { EmptyState, ErrorState } from '@/components/States';
import { useDebounced } from '@/hooks/useDebounced';
import { formatAmount } from '@/lib/format';
import { ProductThumb } from './ProductThumb';
import { QtyField } from './QtyField';
import { hasPack } from './orderUtils';

export interface CatalogSource {
  /** identifies price type / warehouse etc. so caches do not mix */
  key: readonly unknown[];
  search: (
    p: { q: string; category: string; page: number; size: number },
    signal: AbortSignal,
  ) => Promise<{ items: CatalogItem[]; total: number }>;
  categories: () => Promise<{ id: string; name: string | null }[]>;
}

interface Selected {
  item: CatalogItem;
  blocks: number;
  pieces: number;
}

interface Props {
  open: boolean;
  source: CatalogSource;
  fullScreen?: boolean;
  onClose: () => void;
  onAdd: (items: Selected[]) => void;
}

const SIZE = 30;

type View = 'list' | 'grid';
const VIEW_KEY = 'catalog-view';

function readView(): View {
  try {
    return localStorage.getItem(VIEW_KEY) === 'grid' ? 'grid' : 'list';
  } catch {
    return 'list';
  }
}

function saveView(v: View) {
  try {
    localStorage.setItem(VIEW_KEY, v);
  } catch {
    // storage unavailable (private mode): the choice just is not remembered
  }
}

/** Catalog browser: category filter, search, pagination, multi-add with quantities. */
export function AddFromCatalogDialog({ open, source, fullScreen, onClose, onAdd }: Props) {
  const { t } = useTranslation();
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');
  const [page, setPage] = useState(0);
  const [picked, setPicked] = useState<Record<string, Selected>>({});
  const [view, setView] = useState<View>(readView);
  const q = useDebounced(search.trim());

  const cats = useQuery({
    queryKey: ['catalog-categories', ...source.key],
    queryFn: source.categories,
    enabled: open,
    staleTime: 5 * 60_000,
  });
  const params = { q, category, page: page + 1, size: SIZE };
  const list = useQuery({
    queryKey: ['catalog-search', ...source.key, params],
    queryFn: ({ signal }) => source.search(params, signal),
    enabled: open,
    placeholderData: keepPreviousData,
  });

  const items = list.data?.items ?? [];
  const selectedCount = Object.keys(picked).length;

  const setQty = (item: CatalogItem, patch: { blocks?: number; pieces?: number }) =>
    setPicked((cur) => {
      const prev = cur[item.product_ref] ?? { item, blocks: 0, pieces: 0 };
      const next = { ...prev, ...patch };
      const { [item.product_ref]: _drop, ...rest } = cur;
      void _drop;
      return next.blocks === 0 && next.pieces === 0 ? rest : { ...rest, [item.product_ref]: next };
    });

  const blocksField = (it: CatalogItem, sel: Selected | undefined) => (
    <QtyField
      integer
      label={`${t('order.colBlocks')}: ${it.name}`}
      value={sel?.blocks ?? 0}
      disabled={!hasPack(it.pack_quantity)}
      onChange={(blocks) => setQty(it, { blocks })}
    />
  );
  const piecesField = (it: CatalogItem, sel: Selected | undefined) => (
    <QtyField
      label={`${t('order.colPieces')}: ${it.name}`}
      value={sel?.pieces ?? 0}
      onChange={(pieces) => setQty(it, { pieces })}
    />
  );
  const subtitle = (it: CatalogItem) =>
    `${it.category_name ?? ''}${
      hasPack(it.pack_quantity)
        ? ` · ${t('order.perBlock', { n: formatAmount(it.pack_quantity) })}`
        : ''
    }`;

  const close = () => {
    setPicked({});
    onClose();
  };

  return (
    <Dialog open={open} onClose={close} fullWidth maxWidth="lg" fullScreen={fullScreen}>
      <DialogTitle>{t('order.catalogTitle')}</DialogTitle>
      <DialogContent dividers>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} sx={{ mb: 2 }}>
          <TextField
            select
            label={t('order.colCategory')}
            value={category}
            onChange={(e) => {
              setCategory(e.target.value);
              setPage(0);
            }}
            sx={{ minWidth: 220 }}
          >
            <MenuItem value="">{t('common.all')}</MenuItem>
            {(cats.data ?? []).map((c) => (
              <MenuItem key={c.id} value={c.id}>
                {c.name ?? c.id}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(0);
            }}
            label={t('common.search')}
            placeholder={t('order.catalogSearch')}
            fullWidth
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
          <ToggleButtonGroup
            exclusive
            size="small"
            value={view}
            aria-label={t('order.catalogView')}
            onChange={(_, v: View | null) => {
              if (v) {
                setView(v);
                saveView(v);
              }
            }}
            sx={{ alignSelf: { xs: 'flex-end', sm: 'center' }, flexShrink: 0 }}
          >
            <ToggleButton value="list" aria-label={t('order.viewList')} title={t('order.viewList')}>
              <ViewListIcon />
            </ToggleButton>
            <ToggleButton value="grid" aria-label={t('order.viewGrid')} title={t('order.viewGrid')}>
              <GridViewIcon />
            </ToggleButton>
          </ToggleButtonGroup>
        </Stack>
        {list.isError && <ErrorState error={list.error} onRetry={() => void list.refetch()} />}
        {list.isSuccess && items.length === 0 && <EmptyState title={t('order.catalogEmpty')} />}
        {items.length > 0 && view === 'list' && (
          <TableContainer>
            <Table size="small" aria-label={t('order.catalogTitle')}>
              <TableHead>
                <TableRow>
                  <TableCell>{t('order.colPhoto')}</TableCell>
                  <TableCell>{t('order.colProduct')}</TableCell>
                  <TableCell align="right">{t('order.colPrice')}</TableCell>
                  <TableCell align="right">{t('order.colStock')}</TableCell>
                  <TableCell>{t('order.colBlocks')}</TableCell>
                  <TableCell>{t('order.colPieces')}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {items.map((it) => {
                  const sel = picked[it.product_ref];
                  return (
                    <TableRow key={it.product_ref} hover selected={!!sel}>
                      <TableCell>
                        <ProductThumb
                          name={it.name}
                          thumbUrl={it.thumb_url}
                          imageUrl={it.image_url}
                        />
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2" fontWeight={600}>
                          {it.name}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {subtitle(it)}
                        </Typography>
                      </TableCell>
                      <TableCell align="right">
                        {it.price == null ? '—' : formatAmount(it.price)}
                      </TableCell>
                      <TableCell align="right">
                        {it.stock == null ? '—' : formatAmount(it.stock)}
                      </TableCell>
                      <TableCell>{blocksField(it, sel)}</TableCell>
                      <TableCell>{piecesField(it, sel)}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        )}
        {items.length > 0 && view === 'grid' && (
          <Box
            component="ul"
            aria-label={t('order.catalogTitle')}
            sx={{
              listStyle: 'none',
              m: 0,
              p: 0,
              display: 'grid',
              gap: 1.5,
              gridTemplateColumns: {
                xs: 'repeat(2, minmax(0, 1fr))',
                sm: 'repeat(3, minmax(0, 1fr))',
                md: 'repeat(4, minmax(0, 1fr))',
                lg: 'repeat(5, minmax(0, 1fr))',
              },
            }}
          >
            {items.map((it) => {
              const sel = picked[it.product_ref];
              return (
                <Card
                  component="li"
                  key={it.product_ref}
                  variant="outlined"
                  sx={{
                    p: 1,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 0.75,
                    borderColor: sel ? 'primary.main' : 'divider',
                    bgcolor: sel ? 'action.selected' : 'background.paper',
                  }}
                >
                  <ProductThumb
                    fill
                    name={it.name}
                    thumbUrl={it.thumb_url}
                    imageUrl={it.image_url}
                  />
                  <Typography
                    variant="body2"
                    fontWeight={600}
                    title={it.name ?? undefined}
                    sx={{
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical',
                      overflow: 'hidden',
                      minHeight: '2.86em',
                      lineHeight: 1.43,
                    }}
                  >
                    {it.name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" noWrap>
                    {subtitle(it) || '\u00a0'}
                  </Typography>
                  <Stack direction="row" justifyContent="space-between" alignItems="baseline">
                    <Typography variant="subtitle2" fontWeight={700}>
                      {it.price == null ? '—' : formatAmount(it.price)}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {t('order.colStock')}: {it.stock == null ? '—' : formatAmount(it.stock)}
                    </Typography>
                  </Stack>
                  <Stack spacing={0.5} sx={{ mt: 'auto' }}>
                    <Stack
                      direction={{ xs: 'column', sm: 'row' }}
                      alignItems="center"
                      justifyContent="space-between"
                    >
                      <Typography variant="caption" color="text.secondary">
                        {t('order.colBlocks')}
                      </Typography>
                      {blocksField(it, sel)}
                    </Stack>
                    <Stack
                      direction={{ xs: 'column', sm: 'row' }}
                      alignItems="center"
                      justifyContent="space-between"
                    >
                      <Typography variant="caption" color="text.secondary">
                        {t('order.colPieces')}
                      </Typography>
                      {piecesField(it, sel)}
                    </Stack>
                  </Stack>
                </Card>
              );
            })}
          </Box>
        )}
        <Box>
          <TablePagination
            component="div"
            count={list.data?.total ?? 0}
            page={page}
            rowsPerPage={SIZE}
            rowsPerPageOptions={[SIZE]}
            onPageChange={(_, p) => setPage(p)}
            labelDisplayedRows={({ from, to, count }) => `${from}–${to} ${t('common.of')} ${count}`}
          />
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={close}>{t('common.cancel')}</Button>
        <Button
          variant="contained"
          disabled={selectedCount === 0}
          onClick={() => {
            onAdd(Object.values(picked));
            setPicked({});
            onClose();
          }}
        >
          {t('order.catalogAdd', { count: selectedCount })}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

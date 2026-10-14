import SearchIcon from '@mui/icons-material/Search';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
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

/** Catalog browser: category filter, search, pagination, multi-add with quantities. */
export function AddFromCatalogDialog({ open, source, fullScreen, onClose, onAdd }: Props) {
  const { t } = useTranslation();
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');
  const [page, setPage] = useState(0);
  const [picked, setPicked] = useState<Record<string, Selected>>({});
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
        </Stack>
        {list.isError && <ErrorState error={list.error} onRetry={() => void list.refetch()} />}
        {list.isSuccess && items.length === 0 && <EmptyState title={t('order.catalogEmpty')} />}
        {items.length > 0 && (
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
                          {it.category_name ?? ''}
                          {hasPack(it.pack_quantity)
                            ? ` · ${t('order.perBlock', { n: formatAmount(it.pack_quantity) })}`
                            : ''}
                        </Typography>
                      </TableCell>
                      <TableCell align="right">
                        {it.price == null ? '—' : formatAmount(it.price)}
                      </TableCell>
                      <TableCell align="right">
                        {it.stock == null ? '—' : formatAmount(it.stock)}
                      </TableCell>
                      <TableCell>
                        <QtyField
                          integer
                          label={`${t('order.colBlocks')}: ${it.name}`}
                          value={sel?.blocks ?? 0}
                          disabled={!hasPack(it.pack_quantity)}
                          onChange={(blocks) => setQty(it, { blocks })}
                        />
                      </TableCell>
                      <TableCell>
                        <QtyField
                          label={`${t('order.colPieces')}: ${it.name}`}
                          value={sel?.pieces ?? 0}
                          onChange={(pieces) => setQty(it, { pieces })}
                        />
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
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

import SearchIcon from '@mui/icons-material/Search';
import Chip from '@mui/material/Chip';
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
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { productsApi } from '@/api/admin';
import { qk } from '@/api/keys';
import type { AdminProduct } from '@/api/types';
import { PageHeader } from '@/components/PageHeader';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/States';
import { ProductThumb } from '@/features/orders/ProductThumb';
import { useDebounced } from '@/hooks/useDebounced';
import { usePaging } from '@/hooks/usePaging';
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
      sx={{ minWidth: 150 }}
    >
      <MenuItem value="all">{t('common.all')}</MenuItem>
      <MenuItem value="yes">{t('common.yes')}</MenuItem>
      <MenuItem value="no">{t('common.no')}</MenuItem>
    </TextField>
  );
}

export default function AdminProductsPage() {
  const { t } = useTranslation();
  const can = useCan();
  const refs = useRefs();
  const paging = usePaging(25);
  const [search, setSearch] = useState('');
  const [active, setActive] = useState<Tri>('all');
  const [hidden, setHidden] = useState<Tri>('all');
  const [hasBarcode, setHasBarcode] = useState<Tri>('all');
  const [category, setCategory] = useState('');
  const [selected, setSelected] = useState<AdminProduct | null>(null);
  const q = useDebounced(search.trim());

  const params = {
    q: q || undefined,
    active: tri(active),
    hidden: tri(hidden),
    has_barcode: tri(hasBarcode),
    category: category || undefined,
    limit: paging.pageSize,
    offset: paging.offset,
  };
  const list = useQuery({
    queryKey: qk.admin.products.list(params),
    queryFn: ({ signal }) => productsApi.list(params, signal),
    placeholderData: keepPreviousData,
  });
  const items = list.data?.items ?? [];
  const reset =
    <T,>(set: (v: T) => void) =>
    (v: T) => {
      set(v);
      paging.reset();
    };

  return (
    <>
      <PageHeader title={t('nav.products')} />
      <Paper variant="outlined">
        <Stack direction="row" spacing={1.5} useFlexGap flexWrap="wrap" sx={{ p: 2 }}>
          <TextField
            label={t('common.search')}
            placeholder={t('prod.searchHint')}
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              paging.reset();
            }}
            sx={{ minWidth: 260 }}
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
            sx={{ minWidth: 200 }}
          >
            <MenuItem value="">{t('common.all')}</MenuItem>
            {refs.named('categories').map((c) => (
              <MenuItem key={c.id} value={c.id}>
                {c.name ?? c.id}
              </MenuItem>
            ))}
          </TextField>
        </Stack>
        <TableContainer>
          <Table size="small" aria-label={t('nav.products')}>
            <TableHead>
              <TableRow>
                <TableCell>{t('order.colPhoto')}</TableCell>
                <TableCell>{t('order.colProduct')}</TableCell>
                <TableCell>{t('order.colCategory')}</TableCell>
                <TableCell>{t('common.status')}</TableCell>
                <TableCell align="right">{t('prod.barcodes')}</TableCell>
                <TableCell align="right">{t('order.colStock')}</TableCell>
              </TableRow>
            </TableHead>
            {list.isPending ? (
              <TableSkeleton cols={6} />
            ) : (
              <TableBody>
                {items.map((p) => (
                  <TableRow
                    key={p.sd_id}
                    hover
                    tabIndex={0}
                    role="button"
                    aria-label={`${t('prod.open')}: ${p.name}`}
                    onClick={() => setSelected(p)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') setSelected(p);
                    }}
                    sx={{ cursor: 'pointer' }}
                  >
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
                      <Stack direction="row" spacing={0.5}>
                        <Chip
                          size="small"
                          color={p.active ? 'success' : 'default'}
                          label={p.active ? t('prod.active') : t('prod.inactive')}
                        />
                        {p.hidden && <Chip size="small" color="warning" label={t('prod.hidden')} />}
                      </Stack>
                    </TableCell>
                    <TableCell align="right">{p.barcodes.length}</TableCell>
                    <TableCell align="right">
                      {formatAmount(p.stock.reduce((a, s) => a + s.quantity, 0))}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            )}
          </Table>
        </TableContainer>
        {list.isError && <ErrorState error={list.error} onRetry={() => void list.refetch()} />}
        {list.isSuccess && items.length === 0 && <EmptyState title={t('prod.empty')} />}
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
      <ProductDrawer
        product={selected}
        canWrite={can('products.write')}
        onClose={() => setSelected(null)}
        onChange={setSelected}
      />
    </>
  );
}

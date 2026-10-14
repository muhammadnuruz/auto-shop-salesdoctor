import CloseIcon from '@mui/icons-material/Close';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Divider from '@mui/material/Divider';
import Drawer from '@mui/material/Drawer';
import FormControlLabel from '@mui/material/FormControlLabel';
import IconButton from '@mui/material/IconButton';
import Stack from '@mui/material/Stack';
import Switch from '@mui/material/Switch';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useSnackbar } from 'notistack';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { productsApi } from '@/api/admin';
import { ApiError } from '@/api/http';
import { qk } from '@/api/keys';
import type { AdminProduct, AdminProductWithWarnings } from '@/api/types';
import { ProductThumb } from '@/features/orders/ProductThumb';
import { formatAmount } from '@/lib/format';
import { describeError } from '@/lib/errors';

export const BARCODE_RE = /^[0-9]{8,14}$/;

interface Props {
  product: AdminProduct | null;
  canWrite: boolean;
  onClose: () => void;
  onChange: (p: AdminProduct) => void;
}

/** Stock per warehouse, prices per type, hide/unhide + note, barcodes. */
export function ProductDrawer({ product, canWrite, onClose, onChange }: Props) {
  return (
    <Drawer anchor="right" open={!!product} onClose={onClose}>
      {product && (
        <DrawerBody
          key={product.sd_id}
          product={product}
          canWrite={canWrite}
          onClose={onClose}
          onChange={onChange}
        />
      )}
    </Drawer>
  );
}

function DrawerBody({
  product,
  canWrite,
  onClose,
  onChange,
}: {
  product: AdminProduct;
  canWrite: boolean;
  onClose: () => void;
  onChange: (p: AdminProduct) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();
  const [note, setNote] = useState(product.note ?? '');
  const [code, setCode] = useState('');
  const [codeError, setCodeError] = useState<string | null>(null);
  const [others, setOthers] = useState<{ sd_id: string; name: string }[]>([]);

  const done = (p: AdminProduct) => {
    onChange(p);
    void qc.invalidateQueries({ queryKey: qk.admin.products.all() });
  };

  const patch = useMutation({
    mutationFn: (body: { hidden?: boolean; note?: string | null }) =>
      productsApi.patch(product.sd_id, body),
    onSuccess: (p) => {
      done(p);
      enqueueSnackbar(t('common.saved'), { variant: 'success' });
    },
  });
  const add = useMutation({
    mutationFn: (barcode: string) => productsApi.addBarcode(product.sd_id, barcode),
    meta: { silent: true },
    onSuccess: (p: AdminProductWithWarnings) => {
      done(p);
      setCode('');
      setOthers(
        p.warnings?.includes('barcode_used_by_other_products')
          ? (p.other_products ?? []).map((o) => ({ sd_id: o.sd_id, name: o.name ?? o.sd_id }))
          : [],
      );
      enqueueSnackbar(t('prod.barcodeAdded'), { variant: 'success' });
    },
    onError: (e) =>
      setCodeError(
        e instanceof ApiError && e.problemCode === 'barcode_exists'
          ? t('prod.barcodeExists')
          : describeError(e, t),
      ),
  });
  const remove = useMutation({
    mutationFn: (barcode: string) => productsApi.removeBarcode(product.sd_id, barcode),
    onSuccess: (p) => {
      done(p);
      setOthers([]);
    },
  });

  const submitBarcode = () => {
    const value = code.trim();
    if (!BARCODE_RE.test(value)) {
      setCodeError(t('prod.barcodeInvalid'));
      return;
    }
    setCodeError(null);
    add.mutate(value);
  };

  return (
    <Box
      sx={{ width: { xs: '100vw', sm: 480 }, p: 2 }}
      role="dialog"
      aria-label={product.name ?? product.sd_id}
    >
      <Stack direction="row" spacing={1.5} alignItems="flex-start">
        <ProductThumb
          name={product.name}
          thumbUrl={product.thumb_url}
          imageUrl={product.image_url}
          size={64}
        />
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography variant="h3" component="h2">
            {product.name}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {[product.code, product.category_name].filter(Boolean).join(' · ')}
          </Typography>
          <Stack direction="row" spacing={0.5} sx={{ mt: 0.5 }}>
            <Chip
              size="small"
              color={product.active ? 'success' : 'default'}
              label={product.active ? t('prod.active') : t('prod.inactive')}
            />
            {product.hidden && <Chip size="small" color="warning" label={t('prod.hidden')} />}
          </Stack>
        </Box>
        <IconButton onClick={onClose} aria-label={t('common.close')}>
          <CloseIcon />
        </IconButton>
      </Stack>

      <Divider sx={{ my: 2 }} />
      <Typography variant="subtitle2">{t('prod.stockByWarehouse')}</Typography>
      <Table size="small" aria-label={t('prod.stockByWarehouse')}>
        <TableHead>
          <TableRow>
            <TableCell>{t('order.field_warehouse')}</TableCell>
            <TableCell align="right">{t('order.colStock')}</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {product.stock.map((s) => (
            <TableRow key={s.warehouse}>
              <TableCell>{s.name ?? s.warehouse}</TableCell>
              <TableCell align="right">{formatAmount(s.quantity)}</TableCell>
            </TableRow>
          ))}
          {product.stock.length === 0 && (
            <TableRow>
              <TableCell colSpan={2}>{t('prod.noStock')}</TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>

      <Typography variant="subtitle2" sx={{ mt: 2 }}>
        {t('prod.pricesByType')}
      </Typography>
      <Table size="small" aria-label={t('prod.pricesByType')}>
        <TableHead>
          <TableRow>
            <TableCell>{t('order.field_price_type')}</TableCell>
            <TableCell align="right">{t('order.colPrice')}</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {product.prices.map((p) => (
            <TableRow key={p.price_type}>
              <TableCell>{p.name ?? p.price_type}</TableCell>
              <TableCell align="right">{formatAmount(p.price)}</TableCell>
            </TableRow>
          ))}
          {product.prices.length === 0 && (
            <TableRow>
              <TableCell colSpan={2}>{t('prod.noPrices')}</TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>

      <Divider sx={{ my: 2 }} />
      <Typography variant="subtitle2">{t('prod.visibility')}</Typography>
      <FormControlLabel
        control={
          <Switch
            checked={product.hidden}
            disabled={!canWrite || patch.isPending}
            onChange={(_, v) => patch.mutate({ hidden: v })}
          />
        }
        label={t('prod.hideFromShop')}
      />
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <TextField
          label={t('prod.note')}
          value={note}
          disabled={!canWrite}
          onChange={(e) => setNote(e.target.value)}
          multiline
          maxRows={4}
          fullWidth
          size="small"
          slotProps={{ htmlInput: { maxLength: 1000 } }}
        />
        <Button
          variant="outlined"
          disabled={!canWrite || patch.isPending || note === (product.note ?? '')}
          onClick={() => patch.mutate({ note: note.trim() || null })}
        >
          {t('common.save')}
        </Button>
      </Stack>

      <Divider sx={{ my: 2 }} />
      <Typography variant="subtitle2">{t('prod.barcodes')}</Typography>
      <Stack spacing={0.5} sx={{ my: 1 }}>
        {product.barcodes.map((b) => (
          <Stack key={b.barcode} direction="row" alignItems="center" spacing={1}>
            <Typography sx={{ fontFamily: 'monospace' }}>{b.barcode}</Typography>
            <Chip
              size="small"
              variant="outlined"
              label={b.source === 'admin' ? t('prod.srcAdmin') : t('prod.srcSd')}
            />
            {canWrite && b.source === 'admin' && (
              <IconButton
                size="small"
                aria-label={`${t('prod.removeBarcode')}: ${b.barcode}`}
                onClick={() => remove.mutate(b.barcode)}
                disabled={remove.isPending}
              >
                <DeleteOutlineIcon fontSize="small" />
              </IconButton>
            )}
          </Stack>
        ))}
        {product.barcodes.length === 0 && (
          <Typography color="text.secondary">{t('prod.noBarcodes')}</Typography>
        )}
      </Stack>
      {canWrite && (
        <Stack direction="row" spacing={1} alignItems="flex-start">
          <TextField
            label={t('prod.newBarcode')}
            value={code}
            size="small"
            fullWidth
            onChange={(e) => {
              setCode(e.target.value);
              setCodeError(null);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                submitBarcode();
              }
            }}
            error={!!codeError}
            helperText={codeError ?? t('prod.barcodeHelp')}
            slotProps={{ htmlInput: { inputMode: 'numeric', maxLength: 14 } }}
          />
          <Button variant="contained" onClick={submitBarcode} disabled={add.isPending}>
            {t('prod.addBarcode')}
          </Button>
        </Stack>
      )}
      {others.length > 0 && (
        <Alert severity="warning" sx={{ mt: 1.5 }} role="alert">
          {t('prod.barcodeShared')}: {others.map((o) => o.name).join(', ')}
        </Alert>
      )}
    </Box>
  );
}

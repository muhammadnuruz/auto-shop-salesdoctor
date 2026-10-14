import UploadFileIcon from '@mui/icons-material/UploadFile';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { useMutation } from '@tanstack/react-query';
import { useSnackbar } from 'notistack';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ordersApi } from '@/api/shop';
import type { ImportApplyLine, ImportPreview } from '@/api/types';
import { orderErrorMessage } from '@/features/orders/orderUtils';
import { formatAmount } from '@/lib/format';

const MAX_BYTES = 2 * 1024 * 1024;

interface Props {
  open: boolean;
  orderId: string;
  fullScreen?: boolean;
  onApply: (lines: ImportApplyLine[]) => Promise<unknown>;
  onClose: () => void;
}

/** Import from .xlsx/.csv: upload -> preview (matched / ambiguous / unmatched) -> apply. */
export function ImportDialog({ open, orderId, fullScreen, onApply, onClose }: Props) {
  const { t } = useTranslation();
  const { enqueueSnackbar } = useSnackbar();
  const input = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [choice, setChoice] = useState<Record<number, string>>({});
  const [error, setError] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: (file: File) => ordersApi.importPreview(orderId, file),
    meta: { silent: true },
    onSuccess: (p) => {
      setPreview(p);
      setChoice({});
      setError(null);
    },
    onError: (e) => setError(orderErrorMessage(e, t)),
  });
  const apply = useMutation({
    mutationFn: onApply,
    meta: { silent: true },
    onSuccess: () => {
      enqueueSnackbar(t('order.importApplied'), { variant: 'success' });
      close();
    },
    onError: (e) => setError(orderErrorMessage(e, t)),
  });

  const close = () => {
    setPreview(null);
    setChoice({});
    setError(null);
    onClose();
  };

  const onFile = (file: File | undefined) => {
    if (!file) return;
    if (!/\.(xlsx|csv)$/i.test(file.name)) return setError(t('orderErrors.unsupported_type'));
    if (file.size > MAX_BYTES) return setError(t('orderErrors.file_too_large'));
    setError(null);
    upload.mutate(file);
  };

  const lines: ImportApplyLine[] = preview
    ? [
        ...preview.matched.map((m) => ({
          product_ref: m.product_ref,
          blocks: m.blocks,
          pieces: m.pieces,
        })),
        ...preview.ambiguous.flatMap((a) =>
          choice[a.row]
            ? [{ product_ref: choice[a.row] as string, blocks: a.blocks, pieces: a.pieces }]
            : [],
        ),
      ]
    : [];
  // the same product may come from several rows / choices: the server sums them, send merged lines
  const merged = Object.values(
    lines.reduce<Record<string, ImportApplyLine>>((acc, l) => {
      const cur = acc[l.product_ref];
      acc[l.product_ref] = cur
        ? { ...cur, blocks: cur.blocks + l.blocks, pieces: cur.pieces + l.pieces }
        : { ...l };
      return acc;
    }, {}),
  );

  return (
    <Dialog open={open} onClose={close} fullWidth maxWidth="md" fullScreen={fullScreen}>
      <DialogTitle>{t('order.importTitle')}</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2}>
          <Typography color="text.secondary">{t('order.importHelp')}</Typography>
          <input
            ref={input}
            type="file"
            hidden
            accept=".xlsx,.csv"
            data-testid="import-file"
            onChange={(e) => {
              onFile(e.target.files?.[0]);
              e.target.value = '';
            }}
          />
          <Button
            variant="outlined"
            startIcon={<UploadFileIcon />}
            onClick={() => input.current?.click()}
            disabled={upload.isPending}
            sx={{ alignSelf: 'flex-start' }}
          >
            {upload.isPending ? t('common.loading') : t('order.importChoose')}
          </Button>
          {error && <Alert severity="error">{error}</Alert>}

          {preview && (
            <>
              <Typography>
                {t('order.importSummary', {
                  matched: preview.matched.length,
                  ambiguous: preview.ambiguous.length,
                  unmatched: preview.unmatched.length,
                })}
              </Typography>

              {preview.matched.length > 0 && (
                <section aria-label={t('order.importMatched')}>
                  <Typography variant="subtitle2">{t('order.importMatched')}</Typography>
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell>{t('order.colProduct')}</TableCell>
                        <TableCell align="right">{t('order.colBlocks')}</TableCell>
                        <TableCell align="right">{t('order.colPieces')}</TableCell>
                        <TableCell align="right">{t('order.colPrice')}</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {preview.matched.map((m) => (
                        <TableRow key={m.product_ref}>
                          <TableCell>{m.name}</TableCell>
                          <TableCell align="right">{m.blocks}</TableCell>
                          <TableCell align="right">{formatAmount(m.pieces)}</TableCell>
                          <TableCell align="right">
                            {m.price == null ? '—' : formatAmount(m.price)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </section>
              )}

              {preview.ambiguous.length > 0 && (
                <section aria-label={t('order.importAmbiguous')}>
                  <Typography variant="subtitle2">{t('order.importAmbiguous')}</Typography>
                  <Stack spacing={1.5} sx={{ mt: 1 }}>
                    {preview.ambiguous.map((a) => (
                      <TextField
                        key={a.row}
                        select
                        size="small"
                        label={t('order.importRowLabel', {
                          row: a.row,
                          what: a.code ?? a.name ?? '',
                        })}
                        value={choice[a.row] ?? ''}
                        onChange={(e) => setChoice((c) => ({ ...c, [a.row]: e.target.value }))}
                        helperText={t('order.importQty', { blocks: a.blocks, pieces: a.pieces })}
                      >
                        <MenuItem value="">{t('order.importSkip')}</MenuItem>
                        {a.candidates.map((c) => (
                          <MenuItem key={c.product_ref} value={c.product_ref}>
                            {c.name}
                            {c.price != null ? ` · ${formatAmount(c.price)}` : ''}
                          </MenuItem>
                        ))}
                      </TextField>
                    ))}
                  </Stack>
                </section>
              )}

              {preview.unmatched.length > 0 && (
                <section aria-label={t('order.importUnmatched')}>
                  <Typography variant="subtitle2" color="error">
                    {t('order.importUnmatched')}
                  </Typography>
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell>{t('order.importRow')}</TableCell>
                        <TableCell>{t('order.importCodeName')}</TableCell>
                        <TableCell>{t('order.importReason')}</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {preview.unmatched.map((u) => (
                        <TableRow key={u.row}>
                          <TableCell>{u.row}</TableCell>
                          <TableCell>{u.code ?? u.name ?? '—'}</TableCell>
                          <TableCell>
                            {t(`order.importReasons.${u.reason}`, { defaultValue: u.reason })}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </section>
              )}
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={close} disabled={apply.isPending}>
          {t('common.cancel')}
        </Button>
        <Button
          variant="contained"
          disabled={!preview || merged.length === 0 || apply.isPending}
          onClick={() => apply.mutate(merged)}
        >
          {t('order.importApply', { count: merged.length })}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

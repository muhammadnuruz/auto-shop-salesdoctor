import CloseIcon from '@mui/icons-material/Close';
import DownloadIcon from '@mui/icons-material/Download';
import PrintIcon from '@mui/icons-material/Print';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import CircularProgress from '@mui/material/CircularProgress';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import GlobalStyles from '@mui/material/GlobalStyles';
import IconButton from '@mui/material/IconButton';
import Stack from '@mui/material/Stack';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { Invoice } from '@/api/types';
import { ErrorState } from '@/components/States';
import { formatAmount, formatInvoiceDate } from '@/lib/format';

interface Props {
  open: boolean;
  onClose: () => void;
  /** react-query key of this order's waybill */
  queryKey: readonly unknown[];
  load: () => Promise<Invoice>;
  /** server-rendered PDF for the current UI language */
  pdfUrl: string;
  fullScreen?: boolean;
}

const PRINT_CLASS = 'printing-invoice';

// Paper look in both themes; printing shows only the waybill.
const paper = {
  bgcolor: '#fff',
  color: '#111',
  p: { xs: 2, sm: 3 },
  fontSize: 13,
  '& table': { width: '100%', borderCollapse: 'collapse' },
  '& th, & td': { border: '1px solid #555', p: '3px 5px', verticalAlign: 'middle' },
  '& th': { bgcolor: '#eee', fontWeight: 700, textAlign: 'left', whiteSpace: 'nowrap' },
  '& td.num, & th.num': { textAlign: 'right', whiteSpace: 'nowrap' },
  '& tfoot td': { fontWeight: 700, bgcolor: '#f6f6f6' },
} as const;

export function InvoiceDialog({ open, onClose, queryKey, load, pdfUrl, fullScreen }: Props) {
  const { t } = useTranslation();
  const q = useQuery({ queryKey, queryFn: load, enabled: open, staleTime: 60_000 });
  const inv = q.data;

  const print = () => {
    document.body.classList.add(PRINT_CLASS);
    const done = () => {
      document.body.classList.remove(PRINT_CLASS);
      window.removeEventListener('afterprint', done);
    };
    window.addEventListener('afterprint', done);
    window.print();
  };

  const info: [string, string | null][] = inv
    ? [
        [t('invoice.date'), formatInvoiceDate(inv.date)],
        [t('invoice.shipment'), formatInvoiceDate(inv.shipment_date)],
        [t('invoice.client'), inv.client.name],
        [t('invoice.phone'), inv.client.phone],
        [t('invoice.address'), inv.client.address],
        [t('invoice.agent'), inv.agent],
        [t('invoice.priceType'), inv.price_type],
        [t('invoice.warehouse'), inv.warehouse],
        [t('invoice.trade'), inv.trade],
        [t('invoice.code'), inv.code],
      ]
    : [];
  const currency = t('common.currency');

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md" fullScreen={fullScreen}>
      <GlobalStyles
        styles={{
          '@media print': {
            [`body.${PRINT_CLASS} *`]: { visibility: 'hidden' },
            [`body.${PRINT_CLASS} .invoice-print, body.${PRINT_CLASS} .invoice-print *`]: {
              visibility: 'visible',
            },
            [`body.${PRINT_CLASS} .invoice-print`]: {
              position: 'absolute',
              left: 0,
              top: 0,
              width: '100%',
            },
            '@page': { size: 'A4', margin: '12mm' },
          },
        }}
      />
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1, pr: 6 }}>
        <PrintIcon color="primary" />
        {t('invoice.title')}
        <IconButton
          aria-label={t('invoice.close')}
          onClick={onClose}
          sx={{ position: 'absolute', right: 8, top: 8 }}
        >
          <CloseIcon />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers sx={{ bgcolor: 'action.hover' }}>
        {q.isPending && (
          <Stack alignItems="center" sx={{ py: 6 }}>
            <CircularProgress />
          </Stack>
        )}
        {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
        {inv && (
          <Stack spacing={1.5}>
            {inv.warning && <Alert severity="warning">{t(`invoice.${inv.warning}`)}</Alert>}
            <Box className="invoice-print" sx={paper} data-testid="invoice">
              <Box
                component="h2"
                sx={{
                  textAlign: 'center',
                  m: 0,
                  pb: 1,
                  mb: 1.5,
                  fontSize: 22,
                  borderBottom: '2px solid #111',
                }}
              >
                {t('invoice.heading', { n: inv.number })}
              </Box>
              <Box
                component="dl"
                sx={{
                  display: 'grid',
                  gridTemplateColumns: 'max-content 1fr',
                  columnGap: 2,
                  rowGap: 0.25,
                  m: 0,
                  mb: 2,
                  '& dt': { fontWeight: 700 },
                  '& dd': { m: 0 },
                }}
              >
                {info
                  .filter(([, v]) => v)
                  .map(([k, v]) => (
                    <Box key={k} sx={{ display: 'contents' }}>
                      <dt>{k}:</dt>
                      <dd>{v}</dd>
                    </Box>
                  ))}
              </Box>
              <Box sx={{ overflowX: 'auto' }}>
                <table aria-label={t('invoice.heading', { n: inv.number })}>
                  <thead>
                    <tr>
                      <th>{t('invoice.no')}</th>
                      <th>{t('invoice.photo')}</th>
                      <th>{t('invoice.product')}</th>
                      <th>{t('invoice.unit')}</th>
                      <th className="num">{t('invoice.blocks')}</th>
                      <th className="num">{t('invoice.qty')}</th>
                      <th className="num">{t('invoice.price')}</th>
                      <th className="num">{t('invoice.sum')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {inv.lines.map((l) => (
                      <tr key={`${l.position}-${l.product_ref}`}>
                        <td>{l.position}</td>
                        <td style={{ width: 52, textAlign: 'center' }}>
                          {l.thumb_url && (
                            <img
                              src={l.thumb_url}
                              alt=""
                              referrerPolicy="no-referrer"
                              style={{ width: 40, height: 40, objectFit: 'contain' }}
                            />
                          )}
                        </td>
                        <td>{l.name}</td>
                        <td>{l.unit ?? ''}</td>
                        <td className="num">{l.blocks ? formatAmount(l.blocks) : ''}</td>
                        <td className="num">{formatAmount(l.quantity)}</td>
                        <td className="num">{formatAmount(l.price)}</td>
                        <td className="num">{formatAmount(l.sum)}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <td colSpan={5}>{t('invoice.total')}</td>
                      <td className="num">{formatAmount(inv.total_qty)}</td>
                      <td />
                      <td className="num" data-testid="invoice-total">
                        {formatAmount(inv.total_sum)} {currency}
                      </td>
                    </tr>
                    {inv.discount_sum > 0 && (
                      <>
                        <tr>
                          <td colSpan={7}>{t('invoice.discount')}</td>
                          <td className="num">{formatAmount(inv.discount_sum)}</td>
                        </tr>
                        <tr>
                          <td colSpan={7}>{t('invoice.due')}</td>
                          <td className="num">
                            {formatAmount(inv.total_due)} {currency}
                          </td>
                        </tr>
                      </>
                    )}
                  </tfoot>
                </table>
              </Box>
              {inv.comment && (
                <Box sx={{ mt: 2 }}>
                  <strong>{t('invoice.comment')}:</strong> {inv.comment}
                </Box>
              )}
              <Stack direction="row" justifyContent="space-between" sx={{ mt: 4, gap: 2 }}>
                <span>{t('invoice.released')}: ____________________</span>
                <span>{t('invoice.received')}: ____________________</span>
              </Stack>
            </Box>
          </Stack>
        )}
      </DialogContent>
      <DialogActions sx={{ flexWrap: 'wrap', gap: 1 }}>
        <Button variant="contained" startIcon={<PrintIcon />} onClick={print} disabled={!inv}>
          {t('invoice.print')}
        </Button>
        <Button
          variant="outlined"
          startIcon={<DownloadIcon />}
          component="a"
          href={pdfUrl}
          download
          disabled={!inv}
        >
          {t('invoice.pdf')}
        </Button>
        <Button onClick={onClose}>{t('invoice.close')}</Button>
      </DialogActions>
    </Dialog>
  );
}

import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import CircularProgress from '@mui/material/CircularProgress';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { useTranslation } from 'react-i18next';
import { Link as RouterLink } from 'react-router-dom';
import type { Order } from '@/api/types';
import { formatMoney } from '@/lib/format';

export function SentPanel({
  order,
  slug,
  onNew,
  onRepeat,
  busy,
}: {
  order: Order;
  slug: string;
  onNew: () => void;
  onRepeat: () => void;
  busy: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Card variant="outlined" role="status" data-testid="sent-panel">
      <CardContent>
        <Stack alignItems="center" spacing={1.5} sx={{ py: 3, textAlign: 'center' }}>
          <CheckCircleIcon color="success" sx={{ fontSize: 64 }} />
          <Typography variant="h2" component="h1">
            {t('order.sentTitle')}
          </Typography>
          <Typography>
            {t('order.sentNumber')}:{' '}
            <strong data-testid="sd-number">{order.sd_id ?? order.code}</strong>
          </Typography>
          <Typography color="text.secondary">
            {t('order.sentTotals', {
              lines: order.totals.lines_count,
              sum: formatMoney(order.totals.sum, t('common.currency')),
            })}
          </Typography>
          <Stack
            direction={{ xs: 'column', sm: 'row' }}
            spacing={1}
            sx={{ width: { xs: '100%', sm: 'auto' } }}
          >
            <Button variant="contained" onClick={onNew} disabled={busy}>
              {t('order.newOrder')}
            </Button>
            <Button variant="outlined" onClick={onRepeat} disabled={busy}>
              {t('orders.repeat')}
            </Button>
            <Button component={RouterLink} to={`/s/${slug}/orders`}>
              {t('orders.toList')}
            </Button>
          </Stack>
        </Stack>
      </CardContent>
    </Card>
  );
}

export function ProcessingPanel({ order, slug }: { order: Order; slug: string }) {
  const { t } = useTranslation();
  return (
    <Card variant="outlined" role="status" data-testid="processing-panel">
      <CardContent>
        <Stack alignItems="center" spacing={1.5} sx={{ py: 3, textAlign: 'center' }}>
          <CircularProgress aria-label={t('order.processingTitle')} />
          <Typography variant="h2" component="h1">
            {t('order.processingTitle')}
          </Typography>
          <Typography color="text.secondary">{t('order.processingText')}</Typography>
          <Typography variant="body2" color="text.secondary">
            {t('order.code')}: {order.code}
          </Typography>
          <Button component={RouterLink} to={`/s/${slug}/orders`}>
            {t('orders.toList')}
          </Button>
        </Stack>
      </CardContent>
    </Card>
  );
}

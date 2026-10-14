import EditIcon from '@mui/icons-material/Edit';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Collapse from '@mui/material/Collapse';
import IconButton from '@mui/material/IconButton';
import Stack from '@mui/material/Stack';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link as RouterLink } from 'react-router-dom';
import type { Order, ShopMe } from '@/api/types';
import { CategorySummary } from '@/features/orders/CategorySummary';
import { formatMoney } from '@/lib/format';

/** Shop name / phone / balance on the left, per-category summary on the right. */
export function OrderHeaderCard({
  me,
  order,
  slug,
  summaryTotals,
}: {
  me: ShopMe;
  order: Order;
  slug: string;
  summaryTotals: Order['totals'];
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const unit = t('common.currency');
  const breakdown = me.balance_by_type;
  return (
    <Box
      sx={{
        display: 'grid',
        gap: 2,
        gridTemplateColumns: {
          xs: 'minmax(0, 1fr)',
          md: 'minmax(280px, 1fr) minmax(360px, 1.4fr)',
        },
      }}
    >
      <Card variant="outlined">
        <CardContent>
          <Stack direction="row" alignItems="center" spacing={0.5}>
            <Typography variant="h2" component="h1" sx={{ fontSize: '1.25rem' }}>
              {me.client.name ?? t('shop.shopName')}
            </Typography>
            <Tooltip title={t('order.editShop')}>
              <IconButton
                component={RouterLink}
                to={`/s/${slug}/home`}
                size="small"
                aria-label={t('order.editShop')}
              >
                <EditIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Stack>
          <Stack spacing={0.75} sx={{ mt: 1 }}>
            <Stack direction="row" justifyContent="space-between">
              <Typography color="text.secondary">{t('shop.phoneMasked')}</Typography>
              <Typography>{me.client.phone}</Typography>
            </Stack>
            <Stack direction="row" justifyContent="space-between" alignItems="center">
              <Typography color="text.secondary">{t('shop.balance')}</Typography>
              <Stack direction="row" alignItems="center">
                <Typography fontWeight={700} data-testid="balance">
                  {formatMoney(me.client.balance, unit)}
                </Typography>
                {breakdown.length > 0 && (
                  <IconButton
                    size="small"
                    onClick={() => setOpen((v) => !v)}
                    aria-expanded={open}
                    aria-label={t('order.balanceBreakdown')}
                  >
                    {open ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                  </IconButton>
                )}
              </Stack>
            </Stack>
            <Collapse in={open} unmountOnExit>
              <Stack spacing={0.5} sx={{ pl: 1 }}>
                {breakdown.map((b) => (
                  <Stack key={b.payment_type_sd_id} direction="row" justifyContent="space-between">
                    <Typography variant="body2" color="text.secondary">
                      {b.name ?? b.payment_type_sd_id}
                    </Typography>
                    <Typography variant="body2">{formatMoney(b.amount, unit)}</Typography>
                  </Stack>
                ))}
              </Stack>
            </Collapse>
          </Stack>
        </CardContent>
      </Card>
      <Card variant="outlined">
        <CardContent sx={{ p: { xs: 1, sm: 2 } }}>
          <CategorySummary rows={order.categories} totals={summaryTotals} />
        </CardContent>
      </Card>
    </Box>
  );
}

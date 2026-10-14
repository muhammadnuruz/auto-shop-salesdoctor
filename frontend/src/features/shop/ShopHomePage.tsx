import AddShoppingCartIcon from '@mui/icons-material/AddShoppingCart';
import LogoutIcon from '@mui/icons-material/Logout';
import ReceiptLongIcon from '@mui/icons-material/ReceiptLong';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Divider from '@mui/material/Divider';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Link as RouterLink, useNavigate, useParams } from 'react-router-dom';
import { qk } from '@/api/keys';
import { shopApi } from '@/api/shop';
import type { ShopMe } from '@/api/types';
import { formatMoney } from '@/lib/format';
import { ShopShell } from './ShopShell';
import { useShopMe } from './queries';

const GROUPS = ['warehouses', 'price_types', 'agents', 'trade_directions'] as const;

function modeLabel(mode: string, t: (k: string) => string): string {
  return mode === 'fixed'
    ? t('shop.modeFixed')
    : mode === 'choice'
      ? t('shop.modeChoice')
      : t('shop.modeClientAssigned');
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Stack direction="row" justifyContent="space-between" spacing={2} alignItems="baseline">
      <Typography color="text.secondary">{label}</Typography>
      <Typography component="div" sx={{ textAlign: 'right', fontWeight: 500 }}>
        {children}
      </Typography>
    </Stack>
  );
}

function Home({ me }: { me: ShopMe }) {
  const { t } = useTranslation();
  const { slug = '' } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const unit = t('common.currency');

  const logout = useMutation({
    mutationFn: shopApi.logout,
    meta: { silent: true },
    onSettled: () => {
      qc.setQueryData(qk.shop.me(), null);
      navigate(`/s/${slug}`, { replace: true });
    },
  });

  const { limits } = me;
  return (
    <ShopShell title={me.link.title} logoUrl={me.link.logo_url} color={me.link.color}>
      <Stack spacing={2}>
        <Card>
          <CardContent>
            <Typography variant="h1" component="h1" sx={{ fontSize: '1.4rem', mb: 0.5 }}>
              {me.client.name ?? t('shop.shopName')}
            </Typography>
            {me.link.welcome_text && (
              <Typography color="text.secondary">{me.link.welcome_text}</Typography>
            )}
            <Stack spacing={1} sx={{ mt: 2 }}>
              {me.client.address && <Row label={t('shop.address')}>{me.client.address}</Row>}
              <Row label={t('shop.phoneMasked')}>{me.client.phone}</Row>
              <Divider />
              <Row label={t('shop.balance')}>
                <span data-testid="balance">{formatMoney(me.client.balance, unit)}</span>
              </Row>
            </Stack>
          </CardContent>
        </Card>

        <Button
          component={RouterLink}
          to={`/s/${slug}/order/new`}
          variant="contained"
          size="large"
          startIcon={<AddShoppingCartIcon />}
          sx={{ minHeight: 56 }}
        >
          {t('shop.newOrder')}
        </Button>

        <Button
          component={RouterLink}
          to={`/s/${slug}/orders`}
          variant="outlined"
          size="large"
          startIcon={<ReceiptLongIcon />}
        >
          {t('orders.myOrders')}
        </Button>

        <Card>
          <CardContent>
            <Typography variant="h3" component="h2" sx={{ mb: 1 }}>
              {t('shop.options')}
            </Typography>
            <Stack spacing={1.5}>
              {GROUPS.map((g) => {
                const group = me.options[g];
                if (!group) return null;
                return (
                  <Stack key={g} spacing={0.5}>
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Typography fontWeight={500}>{t(`shop.${g}`)}</Typography>
                      <Chip size="small" variant="outlined" label={modeLabel(group.mode, t)} />
                    </Stack>
                    <Typography color="text.secondary">
                      {group.options.length
                        ? group.options.map((o) => o.name ?? o.id).join(', ')
                        : t('shop.noOptions')}
                    </Typography>
                  </Stack>
                );
              })}
            </Stack>
            {(limits.min_order_sum != null ||
              limits.max_order_sum != null ||
              limits.allow_over_stock !== undefined) && (
              <>
                <Divider sx={{ my: 2 }} />
                <Typography variant="h3" component="h2" sx={{ mb: 1 }}>
                  {t('shop.limits')}
                </Typography>
                <Stack spacing={0.5}>
                  {limits.min_order_sum != null && (
                    <Row label={t('shop.minSum')}>{formatMoney(limits.min_order_sum, unit)}</Row>
                  )}
                  {limits.max_order_sum != null && (
                    <Row label={t('shop.maxSum')}>{formatMoney(limits.max_order_sum, unit)}</Row>
                  )}
                  {limits.allow_over_stock !== undefined && (
                    <Row label={t('shop.overStock')}>
                      {limits.allow_over_stock ? t('shop.allowed') : t('shop.forbidden')}
                    </Row>
                  )}
                </Stack>
              </>
            )}
          </CardContent>
        </Card>

        <Button
          onClick={() => logout.mutate()}
          startIcon={<LogoutIcon />}
          disabled={logout.isPending}
        >
          {t('shop.logout')}
        </Button>
      </Stack>
    </ShopShell>
  );
}

export default function ShopHomePage() {
  const me = useShopMe();
  return me.data ? <Home me={me.data} /> : null;
}

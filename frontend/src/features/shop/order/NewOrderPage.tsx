import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import CircularProgress from '@mui/material/CircularProgress';
import Stack from '@mui/material/Stack';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Link as RouterLink, useNavigate, useParams } from 'react-router-dom';
import { qk } from '@/api/keys';
import { ordersApi } from '@/api/shop';
import { orderErrorMessage } from '@/features/orders/orderUtils';
import { ShopShell } from '../ShopShell';
import { useShopMe } from '../queries';

/** /s/:slug/order/new: creates a server-side draft, then continues on /orders/:id. */
export default function NewOrderPage() {
  const { t } = useTranslation();
  const { slug = '' } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const me = useShopMe().data;
  const started = useRef(false);

  const create = useMutation({
    mutationFn: () => ordersApi.create({}),
    meta: { silent: true },
    onSuccess: (o) => {
      qc.setQueryData(qk.shop.orders.detail(o.public_id), o);
      void qc.invalidateQueries({ queryKey: qk.shop.orders.all() });
      navigate(`/s/${slug}/orders/${o.public_id}`, { replace: true });
    },
  });
  const { mutate } = create;
  useEffect(() => {
    if (started.current) return; // StrictMode double effect must not create two drafts
    started.current = true;
    mutate();
  }, [mutate]);

  return (
    <ShopShell title={me?.link.title} logoUrl={me?.link.logo_url} color={me?.link.color}>
      {create.isError ? (
        <Stack spacing={2}>
          <Alert severity="error" role="alert">
            {orderErrorMessage(create.error, t)}
          </Alert>
          <Stack direction="row" spacing={1}>
            <Button variant="contained" onClick={() => create.mutate()}>
              {t('common.retry')}
            </Button>
            <Button component={RouterLink} to={`/s/${slug}/orders`}>
              {t('orders.toList')}
            </Button>
          </Stack>
        </Stack>
      ) : (
        <Box sx={{ display: 'grid', placeItems: 'center', py: 8 }}>
          <CircularProgress aria-label={t('order.creating')} />
        </Box>
      )}
    </ShopShell>
  );
}

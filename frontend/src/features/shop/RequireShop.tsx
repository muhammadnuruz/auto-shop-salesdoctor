import Box from '@mui/material/Box';
import CircularProgress from '@mui/material/CircularProgress';
import { Navigate, Outlet, useParams } from 'react-router-dom';
import { ErrorState } from '@/components/States';
import { ShopShell } from './ShopShell';
import { useShopMe } from './queries';

/** Session guard for /s/:slug/* : not signed in (or other link) -> landing page. */
export default function RequireShop() {
  const { slug = '' } = useParams();
  const me = useShopMe();
  if (me.isPending)
    return (
      <ShopShell>
        <Box sx={{ display: 'grid', placeItems: 'center', py: 8 }}>
          <CircularProgress aria-label="loading" />
        </Box>
      </ShopShell>
    );
  if (me.isError)
    return (
      <ShopShell>
        <ErrorState error={me.error} onRetry={() => void me.refetch()} />
      </ShopShell>
    );
  if (!me.data || me.data.link.slug !== slug) return <Navigate to={`/s/${slug}`} replace />;
  return <Outlet />;
}

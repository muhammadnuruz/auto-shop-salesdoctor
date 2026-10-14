import LinkOffIcon from '@mui/icons-material/LinkOff';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Skeleton from '@mui/material/Skeleton';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { useTranslation } from 'react-i18next';
import { Navigate, useParams } from 'react-router-dom';
import { ApiError } from '@/api/http';
import { ErrorState } from '@/components/States';
import { LoginCard } from './LoginCard';
import { useLinkInfo, useShopMe } from './queries';
import { ShopShell } from './ShopShell';

function Notice({ title, text }: { title: string; text: string }) {
  return (
    <Stack alignItems="center" spacing={1.5} sx={{ py: 6, textAlign: 'center' }} role="status">
      <LinkOffIcon color="disabled" sx={{ fontSize: 56 }} />
      <Typography variant="h2">{title}</Typography>
      <Typography color="text.secondary">{text}</Typography>
    </Stack>
  );
}

export default function LinkLandingPage() {
  const { slug = '' } = useParams();
  const { t } = useTranslation();
  const link = useLinkInfo(slug);
  const me = useShopMe();

  if (me.data && me.data.link.slug === slug) return <Navigate to={`/s/${slug}/home`} replace />;

  const data = link.data;
  const active = data?.state === 'active';
  const notFound = link.error instanceof ApiError && link.error.status === 404;

  return (
    <ShopShell
      title={active ? data.title : null}
      logoUrl={active ? data.logo_url : null}
      color={active ? data.color : null}
    >
      <Card>
        <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
          {link.isPending ? (
            <Stack spacing={2} aria-busy="true">
              <Skeleton variant="text" height={40} />
              <Skeleton variant="rounded" height={56} />
              <Skeleton variant="rounded" height={48} />
            </Stack>
          ) : notFound ? (
            <Notice title={t('shop.linkNotFoundTitle')} text={t('shop.linkNotFoundText')} />
          ) : link.isError ? (
            <ErrorState error={link.error} onRetry={() => void link.refetch()} />
          ) : data?.state === 'active' ? (
            <LoginCard slug={slug} welcome={data.welcome_text} />
          ) : (
            <Notice title={t('shop.linkInactiveTitle')} text={t('shop.linkInactiveText')} />
          )}
        </CardContent>
      </Card>
    </ShopShell>
  );
}

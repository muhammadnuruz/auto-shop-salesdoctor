import DownloadIcon from '@mui/icons-material/Download';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Paper from '@mui/material/Paper';
import Skeleton from '@mui/material/Skeleton';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { useQuery } from '@tanstack/react-query';
import QRCode from 'qrcode';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { linksApi } from '@/api/admin';
import { qk } from '@/api/keys';
import type { AdminLink } from '@/api/types';
import { CopyButton } from '@/components/CopyButton';
import { slugIsGuessable, linkUrl } from '@/lib/slug';

export function SharePanel({ link, warnings }: { link: AdminLink; warnings?: string[] }) {
  const { t } = useTranslation();
  const url = linkUrl(link.slug);
  const [qr, setQr] = useState<string | null>(null);
  const stats = useQuery({
    queryKey: qk.admin.links.stats(link.id),
    queryFn: () => linksApi.stats(link.id),
  });

  useEffect(() => {
    let cancelled = false;
    QRCode.toDataURL(url, { width: 640, margin: 2, errorCorrectionLevel: 'M' })
      .then((d) => !cancelled && setQr(d))
      .catch(() => !cancelled && setQr(null));
    return () => {
      cancelled = true;
    };
  }, [url]);

  const lowEntropy = warnings?.includes('slug_low_entropy') || slugIsGuessable(link.slug);
  const dash = (v: number | null | undefined) => (v == null ? t('links.statNoData') : String(v));

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack spacing={2}>
        <Typography variant="h3" component="h2">
          {t('links.share')}
        </Typography>
        {lowEntropy && <Alert severity="warning">{t('links.slugLowEntropy')}</Alert>}
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <TextField
            label={t('links.fullUrl')}
            value={url}
            slotProps={{ input: { readOnly: true } }}
          />
          <CopyButton value={url} label={t('links.copyUrl')} doneMessage={t('links.urlCopied')} />
        </Stack>
        <Stack alignItems="center" spacing={1}>
          {qr ? (
            <Box
              component="img"
              src={qr}
              alt={`${t('links.qr')}: ${url}`}
              sx={{ width: 220, height: 220, bgcolor: '#fff', borderRadius: 1 }}
            />
          ) : (
            <Skeleton variant="rounded" width={220} height={220} />
          )}
          <Button
            component="a"
            href={qr ?? undefined}
            download={`${link.slug}.png`}
            startIcon={<DownloadIcon />}
            disabled={!qr}
            variant="outlined"
          >
            {t('links.downloadQr')}
          </Button>
        </Stack>
        <Box>
          <Typography variant="subtitle1" fontWeight={600} sx={{ mb: 1 }}>
            {t('links.stats')}
          </Typography>
          {stats.isPending ? (
            <Skeleton variant="rounded" height={72} />
          ) : stats.isError ? (
            <Alert severity="warning">{t('errors.loadFailed')}</Alert>
          ) : (
            <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 1 }}>
              {[
                [
                  t('links.statLogins'),
                  stats.data.max_logins
                    ? `${stats.data.login_count} / ${stats.data.max_logins}`
                    : String(stats.data.login_count),
                ],
                [t('links.statActiveSessions'), String(stats.data.active_sessions)],
                [t('links.statOrders'), dash(stats.data.orders)],
              ].map(([label, value]) => (
                <Paper key={label} variant="outlined" sx={{ p: 1, textAlign: 'center' }}>
                  <Typography variant="h3" component="div">
                    {value}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {label}
                  </Typography>
                </Paper>
              ))}
            </Box>
          )}
        </Box>
      </Stack>
    </Paper>
  );
}

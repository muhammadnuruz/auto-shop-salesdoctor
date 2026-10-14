import Button from '@mui/material/Button';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { useTranslation } from 'react-i18next';
import { useRouteError } from 'react-router-dom';

/** Router-level errorElement: lazy-chunk failures, loader errors, render crashes. */
export function RouteError() {
  const { t } = useTranslation();
  const error = useRouteError();
  console.error('Route error', error);
  return (
    <Stack
      role="alert"
      spacing={2}
      alignItems="center"
      justifyContent="center"
      sx={{ minHeight: '60vh', p: 3, textAlign: 'center' }}
    >
      <Typography variant="h2">{t('errors.boundaryTitle')}</Typography>
      <Typography color="text.secondary">{t('errors.boundaryText')}</Typography>
      <Button variant="contained" onClick={() => window.location.reload()}>
        {t('errors.reload')}
      </Button>
    </Stack>
  );
}

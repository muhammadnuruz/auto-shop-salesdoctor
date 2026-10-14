import Button from '@mui/material/Button';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { useTranslation } from 'react-i18next';
import { Link as RouterLink } from 'react-router-dom';

export function NotFoundPage() {
  const { t } = useTranslation();
  return (
    <Stack
      alignItems="center"
      justifyContent="center"
      spacing={2}
      sx={{ minHeight: '60vh', textAlign: 'center', p: 2 }}
    >
      <Typography variant="h1" component="h1">
        404
      </Typography>
      <Typography color="text.secondary">{t('errors.pageNotFound')}</Typography>
      <Button component={RouterLink} to="/" variant="contained">
        {t('errors.goHome')}
      </Button>
    </Stack>
  );
}

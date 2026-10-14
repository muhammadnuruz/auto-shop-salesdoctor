import Typography from '@mui/material/Typography';
import { useQueryClient } from '@tanstack/react-query';
import { useSnackbar } from 'notistack';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { AuthShell } from './AuthShell';
import { PasswordChangeForm } from './PasswordChangeForm';
import { refreshAdminMe } from './queries';

/** Forced change screen (must_change_password). */
export default function ChangePasswordPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  return (
    <AuthShell title={t('adminAuth.changeTitle')}>
      <Typography color="text.secondary">{t('adminAuth.changeText')}</Typography>
      <PasswordChangeForm
        onChanged={() => {
          enqueueSnackbar(t('adminAuth.passwordChanged'), { variant: 'success' });
          void refreshAdminMe(qc).then(() => navigate('/admin', { replace: true }));
        }}
      />
    </AuthShell>
  );
}

import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogContentText from '@mui/material/DialogContentText';
import DialogTitle from '@mui/material/DialogTitle';
import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

interface Props {
  open: boolean;
  title: string;
  message?: ReactNode;
  confirmLabel?: string;
  destructive?: boolean;
  loading?: boolean;
  onConfirm: () => void;
  onClose: () => void;
  children?: ReactNode;
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel,
  destructive,
  loading,
  onConfirm,
  onClose,
  children,
}: Props) {
  const { t } = useTranslation();
  const id = useId();
  return (
    <Dialog
      open={open}
      onClose={loading ? undefined : onClose}
      aria-labelledby={`${id}-t`}
      aria-describedby={`${id}-d`}
      fullWidth
      maxWidth="xs"
    >
      <DialogTitle id={`${id}-t`}>{title}</DialogTitle>
      <DialogContent>
        {message && <DialogContentText id={`${id}-d`}>{message}</DialogContentText>}
        {children}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={loading}>
          {t('common.cancel')}
        </Button>
        <Button
          onClick={onConfirm}
          variant="contained"
          color={destructive ? 'error' : 'primary'}
          disabled={loading}
          autoFocus
        >
          {confirmLabel ?? t('common.confirm')}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

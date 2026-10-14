import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import Divider from '@mui/material/Divider';
import FormControlLabel from '@mui/material/FormControlLabel';
import FormGroup from '@mui/material/FormGroup';
import Switch from '@mui/material/Switch';
import Typography from '@mui/material/Typography';
import { useTranslation } from 'react-i18next';
import { OPTIONAL_COLUMNS, type OrderSettings } from '@/features/orders/useOrderSettings';

interface Props {
  open: boolean;
  settings: OrderSettings;
  onChange: (s: OrderSettings) => void;
  onClose: () => void;
}

export function SettingsDialog({ open, settings, onChange, onClose }: Props) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>{t('order.settings')}</DialogTitle>
      <DialogContent>
        <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
          {t('order.settingsColumns')}
        </Typography>
        <FormGroup>
          {OPTIONAL_COLUMNS.map((c) => (
            <FormControlLabel
              key={c}
              control={
                <Switch
                  checked={settings.columns[c]}
                  onChange={(_, v) =>
                    onChange({ ...settings, columns: { ...settings.columns, [c]: v } })
                  }
                />
              }
              label={t(`order.col_${c}`)}
            />
          ))}
        </FormGroup>
        <Divider sx={{ my: 1.5 }} />
        <FormGroup>
          <FormControlLabel
            control={
              <Switch
                checked={settings.sound}
                onChange={(_, v) => onChange({ ...settings, sound: v })}
              />
            }
            label={t('order.settingsSound')}
          />
          <FormControlLabel
            control={
              <Switch
                checked={settings.autoFocus}
                onChange={(_, v) => onChange({ ...settings, autoFocus: v })}
              />
            }
            label={t('order.settingsAutoFocus')}
          />
        </FormGroup>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t('common.close')}</Button>
      </DialogActions>
    </Dialog>
  );
}

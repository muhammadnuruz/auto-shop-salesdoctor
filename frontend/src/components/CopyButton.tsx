import CheckIcon from '@mui/icons-material/Check';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import IconButton from '@mui/material/IconButton';
import Tooltip from '@mui/material/Tooltip';
import { useSnackbar } from 'notistack';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

export function CopyButton({
  value,
  label,
  doneMessage,
}: {
  value: string;
  label?: string;
  doneMessage?: string;
}) {
  const { t } = useTranslation();
  const { enqueueSnackbar } = useSnackbar();
  const [done, setDone] = useState(false);
  const text = label ?? t('common.copy');

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // clipboard API unavailable (http / permissions): legacy fallback
      const ta = document.createElement('textarea');
      ta.value = value;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    setDone(true);
    enqueueSnackbar(doneMessage ?? t('common.copied'), { variant: 'success' });
    setTimeout(() => setDone(false), 1500);
  };

  return (
    <Tooltip title={text}>
      <IconButton aria-label={text} onClick={() => void copy()} size="small">
        {done ? (
          <CheckIcon fontSize="small" color="success" />
        ) : (
          <ContentCopyIcon fontSize="small" />
        )}
      </IconButton>
    </Tooltip>
  );
}

import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import Typography from '@mui/material/Typography';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

interface Props {
  open: boolean;
  onClose: () => void;
  /** called for every new code (the same code is ignored for 2 s) */
  onDetect: (code: string) => void;
}

/** Camera barcode scanner (EAN-13 / EAN-8 / Code128) via @zxing/browser, loaded on demand. */
export function CameraScanDialog({ open, onClose, onDetect }: Props) {
  const { t } = useTranslation();
  const video = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [last, setLast] = useState<string | null>(null);
  const detect = useRef(onDetect);
  useEffect(() => {
    detect.current = onDetect;
  });

  useEffect(() => {
    if (!open) return;
    let stopped = false;
    let stop: (() => void) | null = null;
    const seen = { code: '', at: 0 };
    void (async () => {
      try {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error('unsupported');
        const [{ BrowserMultiFormatReader }, { BarcodeFormat, DecodeHintType }] = await Promise.all(
          [import('@zxing/browser'), import('@zxing/library')],
        );
        const hints = new Map();
        hints.set(DecodeHintType.POSSIBLE_FORMATS, [
          BarcodeFormat.EAN_13,
          BarcodeFormat.EAN_8,
          BarcodeFormat.CODE_128,
        ]);
        const reader = new BrowserMultiFormatReader(hints);
        // the dialog content mounts after `open`: wait for the <video> element
        await new Promise((r) => setTimeout(r, 0));
        if (stopped || !video.current) return;
        const controls = await reader.decodeFromConstraints(
          { video: { facingMode: { ideal: 'environment' } } },
          video.current,
          (result) => {
            if (!result) return;
            const code = result.getText();
            const now = Date.now();
            if (code === seen.code && now - seen.at < 2000) return;
            seen.code = code;
            seen.at = now;
            setLast(code);
            detect.current(code);
          },
        );
        if (stopped) controls.stop();
        else stop = () => controls.stop();
      } catch {
        if (!stopped) setError(t('order.cameraError'));
      }
    })();
    return () => {
      stopped = true;
      stop?.();
      setError(null);
      setLast(null);
    };
  }, [open, t]);

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>{t('order.cameraTitle')}</DialogTitle>
      <DialogContent>
        {error ? (
          <Alert severity="error">{error}</Alert>
        ) : (
          <Box
            component="video"
            ref={video}
            muted
            playsInline
            sx={{ width: '100%', borderRadius: 1, bgcolor: 'common.black', minHeight: 220 }}
          />
        )}
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }} aria-live="polite">
          {last ? t('order.cameraLast', { code: last }) : t('order.cameraHint')}
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t('common.close')}</Button>
      </DialogActions>
    </Dialog>
  );
}

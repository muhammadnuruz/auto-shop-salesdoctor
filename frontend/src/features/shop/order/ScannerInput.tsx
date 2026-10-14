import QrCodeScannerIcon from '@mui/icons-material/QrCodeScanner';
import InputAdornment from '@mui/material/InputAdornment';
import TextField from '@mui/material/TextField';
import { useEffect, useRef, useState, type FocusEvent } from 'react';
import { useTranslation } from 'react-i18next';

interface Props {
  onScan: (code: string) => void | Promise<void>;
  /** keep the focus on the field (hardware TSD scanners type into the focused input) */
  alwaysFocus: boolean;
  /** pause auto-focus while a dialog is open */
  paused?: boolean;
  big?: boolean;
  disabled?: boolean;
}

const EDITABLE = 'input, textarea, select, button, [contenteditable="true"]';

/** Barcode field for TSD scanners: the scanner "types" the code and presses Enter. */
export function ScannerInput({ onScan, alwaysFocus, paused, big, disabled }: Props) {
  const { t } = useTranslation();
  const [value, setValue] = useState('');
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (alwaysFocus && !paused && !disabled) ref.current?.focus();
  }, [alwaysFocus, paused, disabled]);

  const submit = async () => {
    const code = value.trim();
    if (!code) return;
    setValue('');
    await onScan(code);
    if (alwaysFocus && !paused) ref.current?.focus();
  };

  const onBlur = (e: FocusEvent<HTMLInputElement>) => {
    if (!alwaysFocus || paused || disabled) return;
    const next = e.relatedTarget as HTMLElement | null;
    // focus moved to another control (qty field, dialog...): let the user work there
    if (next?.matches(EDITABLE)) return;
    setTimeout(() => ref.current?.focus(), 0);
  };

  return (
    <TextField
      inputRef={ref}
      value={value}
      disabled={disabled}
      onChange={(e) => setValue(e.target.value)}
      onBlur={onBlur}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          void submit();
        }
      }}
      label={t('order.scanner')}
      placeholder={t('order.scannerPlaceholder')}
      fullWidth
      slotProps={{
        input: {
          startAdornment: (
            <InputAdornment position="start">
              <QrCodeScannerIcon />
            </InputAdornment>
          ),
          sx: big ? { fontSize: '1.35rem', minHeight: 64 } : undefined,
        },
        htmlInput: {
          inputMode: 'text',
          autoComplete: 'off',
          autoCapitalize: 'off',
          maxLength: 64,
          'data-testid': 'scanner-input',
        },
      }}
    />
  );
}

import InputAdornment from '@mui/material/InputAdornment';
import TextField, { type TextFieldProps } from '@mui/material/TextField';
import { formatNational, nationalDigits } from '@/lib/phone';

type Props = Omit<TextFieldProps, 'value' | 'onChange' | 'type'> & {
  /** 9 national digits */
  value: string;
  onDigitsChange: (digits: string) => void;
};

/** +998 prefix is fixed in an adornment; the user types / pastes the rest, shown as XX XXX XX XX. */
export function PhoneField({ value, onDigitsChange, slotProps, ...rest }: Props) {
  return (
    <TextField
      {...rest}
      type="tel"
      value={formatNational(value)}
      onChange={(e) => onDigitsChange(nationalDigits(e.target.value))}
      onPaste={(e) => {
        e.preventDefault();
        onDigitsChange(nationalDigits(e.clipboardData.getData('text')));
      }}
      placeholder="90 123 45 67"
      slotProps={{
        ...slotProps,
        input: {
          startAdornment: <InputAdornment position="start">+998</InputAdornment>,
          ...(slotProps?.input as object),
        },
        htmlInput: {
          inputMode: 'numeric',
          autoComplete: 'tel-national',
          maxLength: 12,
          ...(slotProps?.htmlInput as object),
        },
      }}
    />
  );
}

import Visibility from '@mui/icons-material/Visibility';
import VisibilityOff from '@mui/icons-material/VisibilityOff';
import IconButton from '@mui/material/IconButton';
import InputAdornment from '@mui/material/InputAdornment';
import TextField, { type TextFieldProps } from '@mui/material/TextField';
import { forwardRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

export const PasswordField = forwardRef<HTMLInputElement, Omit<TextFieldProps, 'type'>>(
  function PasswordField({ slotProps, ...rest }, ref) {
    const { t } = useTranslation();
    const [show, setShow] = useState(false);
    return (
      <TextField
        {...rest}
        inputRef={ref}
        type={show ? 'text' : 'password'}
        slotProps={{
          ...slotProps,
          input: {
            endAdornment: (
              <InputAdornment position="end">
                <IconButton
                  edge="end"
                  size="small"
                  onClick={() => setShow((s) => !s)}
                  aria-label={show ? t('common.hidePassword') : t('common.showPassword')}
                >
                  {show ? <VisibilityOff fontSize="small" /> : <Visibility fontSize="small" />}
                </IconButton>
              </InputAdornment>
            ),
          },
        }}
      />
    );
  },
);

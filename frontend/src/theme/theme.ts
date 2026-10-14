import { createTheme, type PaletteMode, type Theme } from '@mui/material/styles';

export function buildTheme(mode: PaletteMode, primary?: string | null): Theme {
  const main = primary && /^#[0-9a-fA-F]{6}$/.test(primary) ? primary : undefined;
  const dark = mode === 'dark';
  return createTheme({
    cssVariables: false,
    palette: {
      mode,
      primary: { main: main ?? (dark ? '#4db6ac' : '#00796b') },
      secondary: { main: dark ? '#ffb74d' : '#ef6c00' },
      background: dark
        ? { default: '#101716', paper: '#182220' }
        : { default: '#f3f6f6', paper: '#ffffff' },
    },
    shape: { borderRadius: 8 },
    typography: {
      fontFamily: '"Roboto","Helvetica Neue",Arial,sans-serif',
      h1: { fontSize: '1.75rem', fontWeight: 600 },
      h2: { fontSize: '1.4rem', fontWeight: 600 },
      h3: { fontSize: '1.2rem', fontWeight: 600 },
      button: { textTransform: 'none', fontWeight: 600 },
    },
    components: {
      MuiButton: { defaultProps: { disableElevation: true } },
      MuiTextField: { defaultProps: { size: 'small', fullWidth: true } },
      MuiFormControl: { defaultProps: { size: 'small' } },
      MuiPaper: { styleOverrides: { root: { backgroundImage: 'none' } } },
      MuiTableCell: { styleOverrides: { head: { fontWeight: 600, whiteSpace: 'nowrap' } } },
      MuiCssBaseline: {
        styleOverrides: {
          ':focus-visible': { outlineOffset: 2 },
        },
      },
    },
  });
}

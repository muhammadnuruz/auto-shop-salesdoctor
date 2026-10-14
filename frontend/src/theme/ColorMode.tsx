import CssBaseline from '@mui/material/CssBaseline';
import { ThemeProvider, type PaletteMode } from '@mui/material/styles';
import useMediaQuery from '@mui/material/useMediaQuery';
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import { buildTheme } from './theme';

const KEY = 'autoshop.theme';

interface Ctx {
  mode: PaletteMode;
  toggle: () => void;
  setBrandColor: (c: string | null) => void;
}
const ColorModeContext = createContext<Ctx | null>(null);

export function ColorModeProvider({ children }: { children: ReactNode }) {
  const prefersDark = useMediaQuery('(prefers-color-scheme: dark)');
  const [stored, setStored] = useState<PaletteMode | null>(() => {
    try {
      const v = localStorage.getItem(KEY);
      return v === 'dark' || v === 'light' ? v : null;
    } catch {
      return null;
    }
  });
  const [brand, setBrand] = useState<string | null>(null);
  const mode: PaletteMode = stored ?? (prefersDark ? 'dark' : 'light');

  const toggle = useCallback(() => {
    const next: PaletteMode = mode === 'dark' ? 'light' : 'dark';
    setStored(next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* ignore */
    }
  }, [mode]);

  const theme = useMemo(() => buildTheme(mode, brand), [mode, brand]);
  const value = useMemo<Ctx>(() => ({ mode, toggle, setBrandColor: setBrand }), [mode, toggle]);

  return (
    <ColorModeContext.Provider value={value}>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        {children}
      </ThemeProvider>
    </ColorModeContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useColorMode(): Ctx {
  const ctx = useContext(ColorModeContext);
  if (!ctx) throw new Error('useColorMode outside ColorModeProvider');
  return ctx;
}

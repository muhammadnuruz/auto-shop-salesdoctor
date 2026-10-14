import StorefrontIcon from '@mui/icons-material/Storefront';
import AppBar from '@mui/material/AppBar';
import Box from '@mui/material/Box';
import Container from '@mui/material/Container';
import Toolbar from '@mui/material/Toolbar';
import Typography from '@mui/material/Typography';
import { useEffect, type ReactNode } from 'react';
import { PrefsControls } from '@/components/PrefsControls';
import { useColorMode } from '@/theme/ColorMode';

interface Props {
  title?: string | null;
  logoUrl?: string | null;
  color?: string | null;
  /** wide layout for the order screen (default: narrow card column) */
  wide?: boolean;
  children: ReactNode;
}

export function ShopShell({ title, logoUrl, color, wide, children }: Props) {
  const { setBrandColor } = useColorMode();
  useEffect(() => {
    setBrandColor(color ?? null);
    return () => setBrandColor(null);
  }, [color, setBrandColor]);

  return (
    <Box sx={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      <AppBar position="static" color="primary" elevation={0}>
        <Toolbar sx={{ gap: 1.5 }}>
          {logoUrl ? (
            <Box
              component="img"
              src={logoUrl}
              alt=""
              sx={{ height: 32, maxWidth: 120, objectFit: 'contain' }}
              referrerPolicy="no-referrer"
            />
          ) : (
            <StorefrontIcon aria-hidden />
          )}
          <Typography variant="h3" component="span" sx={{ flexGrow: 1, fontSize: '1.1rem' }} noWrap>
            {title || 'Auto-Shop'}
          </Typography>
          <PrefsControls />
        </Toolbar>
      </AppBar>
      <Container
        component="main"
        id="main"
        maxWidth={wide ? false : 'sm'}
        sx={
          wide
            ? {
                py: { xs: 1.5, sm: 3 },
                px: { xs: 1, sm: 3 },
                flexGrow: 1,
                maxWidth: 1600,
                minWidth: 0,
              }
            : { py: { xs: 2, sm: 4 }, flexGrow: 1 }
        }
      >
        {children}
      </Container>
    </Box>
  );
}

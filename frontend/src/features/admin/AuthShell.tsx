import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import type { ReactNode } from 'react';
import { PrefsControls } from '@/components/PrefsControls';

export function AuthShell({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Box sx={{ minHeight: '100vh', display: 'grid', placeItems: 'center', p: 2 }}>
      <Box sx={{ position: 'absolute', top: 8, right: 8 }}>
        <PrefsControls />
      </Box>
      <Card sx={{ width: '100%', maxWidth: 420 }}>
        <CardContent sx={{ p: { xs: 2.5, sm: 4 } }}>
          <Stack spacing={2} component="main" id="main">
            <Typography variant="h3" color="primary" sx={{ fontWeight: 700 }}>
              Auto-Shop
            </Typography>
            <Typography variant="h2" component="h1">
              {title}
            </Typography>
            {children}
          </Stack>
        </CardContent>
      </Card>
    </Box>
  );
}

import LogoutIcon from '@mui/icons-material/Logout';
import MenuIcon from '@mui/icons-material/Menu';
import AppBar from '@mui/material/AppBar';
import Box from '@mui/material/Box';
import Divider from '@mui/material/Divider';
import Drawer from '@mui/material/Drawer';
import IconButton from '@mui/material/IconButton';
import List from '@mui/material/List';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import Toolbar from '@mui/material/Toolbar';
import Typography from '@mui/material/Typography';
import useMediaQuery from '@mui/material/useMediaQuery';
import { useTheme } from '@mui/material/styles';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Suspense, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { authApi } from '@/api/admin';
import { qk } from '@/api/keys';
import { PrefsControls } from '@/components/PrefsControls';
import { useCurrentAdmin } from './AdminContext';
import { NAV_ITEMS } from './nav';

const WIDTH = 248;

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useTranslation();
  const user = useCurrentAdmin();
  const items = NAV_ITEMS.filter((i) => !i.perm || user.permissions.includes(i.perm));
  return (
    <Box component="nav" aria-label={t('nav.admin')}>
      <Toolbar>
        <Typography variant="h3" component="span" color="primary" sx={{ fontWeight: 700 }}>
          Auto-Shop
        </Typography>
      </Toolbar>
      <Divider />
      <List>
        {items.map(({ to, labelKey, icon: Icon, end }) => (
          <ListItemButton
            key={to}
            component={NavLink}
            to={to}
            end={end}
            onClick={onNavigate}
            sx={{
              '&.active': {
                bgcolor: 'action.selected',
                color: 'primary.main',
                '& .MuiListItemIcon-root': { color: 'primary.main' },
              },
            }}
          >
            <ListItemIcon sx={{ minWidth: 40 }}>
              <Icon fontSize="small" />
            </ListItemIcon>
            <ListItemText primary={t(labelKey)} />
          </ListItemButton>
        ))}
      </List>
    </Box>
  );
}

export default function AdminLayout() {
  const { t } = useTranslation();
  const theme = useTheme();
  const desktop = useMediaQuery(theme.breakpoints.up('md'));
  const [open, setOpen] = useState(false);
  const user = useCurrentAdmin();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const logout = useMutation({
    mutationFn: authApi.logout,
    meta: { silent: true },
    onSettled: () => {
      qc.removeQueries({ queryKey: qk.admin.all, predicate: (q) => q.queryKey[1] !== 'me' });
      qc.setQueryData(qk.admin.me(), null);
      navigate('/admin/login', { replace: true });
    },
  });

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh' }}>
      <Box
        component="a"
        href="#main"
        sx={{
          position: 'absolute',
          left: -9999,
          '&:focus': { left: 8, top: 8, zIndex: 3000, bgcolor: 'background.paper', p: 1 },
        }}
      >
        {t('common.skipToContent')}
      </Box>
      <AppBar
        position="fixed"
        color="inherit"
        elevation={0}
        sx={{
          ml: { md: `${WIDTH}px` },
          width: { md: `calc(100% - ${WIDTH}px)` },
          borderBottom: 1,
          borderColor: 'divider',
        }}
      >
        <Toolbar sx={{ gap: 1 }}>
          {!desktop && (
            <IconButton
              edge="start"
              onClick={() => setOpen(true)}
              aria-label={t('common.openMenu')}
            >
              <MenuIcon />
            </IconButton>
          )}
          <Box sx={{ flexGrow: 1 }} />
          <PrefsControls />
          <Typography
            variant="body2"
            sx={{ display: { xs: 'none', sm: 'block' }, ml: 1 }}
            data-testid="whoami"
          >
            {user.full_name || user.username}
          </Typography>
          <IconButton
            onClick={() => logout.mutate()}
            aria-label={t('nav.logout')}
            disabled={logout.isPending}
          >
            <LogoutIcon />
          </IconButton>
        </Toolbar>
      </AppBar>
      <Drawer
        variant={desktop ? 'permanent' : 'temporary'}
        open={desktop || open}
        onClose={() => setOpen(false)}
        ModalProps={{ keepMounted: false }}
        sx={{
          width: { md: WIDTH },
          flexShrink: 0,
          '& .MuiDrawer-paper': { width: WIDTH, boxSizing: 'border-box' },
        }}
      >
        <Sidebar onNavigate={() => setOpen(false)} />
      </Drawer>
      <Box
        component="main"
        id="main"
        tabIndex={-1}
        sx={{ flexGrow: 1, minWidth: 0, p: { xs: 1.5, sm: 3 }, outline: 'none' }}
      >
        <Toolbar />
        <Suspense fallback={null}>
          <Outlet />
        </Suspense>
      </Box>
    </Box>
  );
}

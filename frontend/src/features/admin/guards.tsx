import Box from '@mui/material/Box';
import CircularProgress from '@mui/material/CircularProgress';
import type { ReactNode } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { ErrorState, ForbiddenState } from '@/components/States';
import { AdminUserContext, useCan } from './AdminContext';
import { useAdminMe } from './queries';

function Loading() {
  return (
    <Box sx={{ display: 'grid', placeItems: 'center', minHeight: '60vh' }}>
      <CircularProgress aria-label="loading" />
    </Box>
  );
}

/** Requires a signed-in admin. `allowForcedChange` lets the password-change screen through. */
export function RequireAdmin({
  allowForcedChange = false,
  children,
}: {
  allowForcedChange?: boolean;
  children?: ReactNode;
}) {
  const me = useAdminMe();
  const location = useLocation();
  if (me.isPending) return <Loading />;
  if (me.isError) return <ErrorState error={me.error} onRetry={() => void me.refetch()} />;
  if (!me.data) return <Navigate to="/admin/login" replace state={{ from: location.pathname }} />;
  if (me.data.user.must_change_password && !allowForcedChange)
    return <Navigate to="/admin/change-password" replace />;
  return (
    <AdminUserContext.Provider value={me.data.user}>
      {children ?? <Outlet />}
    </AdminUserContext.Provider>
  );
}

export function RequirePermission({ perm }: { perm: string }) {
  const can = useCan();
  return can(perm) ? <Outlet /> : <ForbiddenState />;
}

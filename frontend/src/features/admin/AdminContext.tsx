import { createContext, useContext } from 'react';
import type { AdminUser } from '@/api/types';

export const AdminUserContext = createContext<AdminUser | null>(null);

/** Current admin (only valid below <RequireAdmin/>). */
export function useCurrentAdmin(): AdminUser {
  const u = useContext(AdminUserContext);
  if (!u) throw new Error('useCurrentAdmin outside admin layout');
  return u;
}

export const useCan = () => {
  const u = useCurrentAdmin();
  return (perm: string) => u.permissions.includes(perm);
};

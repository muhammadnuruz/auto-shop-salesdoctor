import type { AdminUser, Role } from '@/api/types';

export const ALL_PERMISSIONS = [
  'links.read',
  'links.write',
  'orders.read',
  'orders.write',
  'orders.cancel',
  'clients.read',
  'clients.write',
  'products.read',
  'products.write',
  'sync.read',
  'sync.run',
  'settings.write',
  'admins.manage',
  'audit.read',
  'reports.read',
] as const;

export const ROLES: Role[] = ['superadmin', 'admin', 'operator', 'viewer'];

/** Mirror of backend app/domain/permissions.py (used only to hide controls; server enforces). */
export const ROLE_PERMISSIONS: Record<Role, readonly string[]> = {
  superadmin: ALL_PERMISSIONS,
  admin: ALL_PERMISSIONS.filter((p) => p !== 'admins.manage'),
  operator: [
    'orders.read',
    'orders.write',
    'orders.cancel',
    'clients.read',
    'products.read',
    'links.read',
  ],
  viewer: [
    'links.read',
    'orders.read',
    'clients.read',
    'products.read',
    'reports.read',
    'sync.read',
  ],
};

export const hasPermission = (user: Pick<AdminUser, 'permissions'> | null, perm: string): boolean =>
  !!user && user.permissions.includes(perm);

/** Roles/permissions an actor may grant: only what they hold themselves (superadmin: all). */
export function grantableRoles(actor: AdminUser): Role[] {
  if (actor.role === 'superadmin') return ROLES;
  return ROLES.filter(
    (r) => r !== 'superadmin' && ROLE_PERMISSIONS[r].every((p) => actor.permissions.includes(p)),
  );
}

export function canManageTarget(actor: AdminUser, target: AdminUser): boolean {
  if (actor.role === 'superadmin') return true;
  return (
    target.permissions.every((p) => actor.permissions.includes(p)) && target.role !== 'superadmin'
  );
}

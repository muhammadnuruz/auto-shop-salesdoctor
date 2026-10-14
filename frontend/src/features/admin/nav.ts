import AdminPanelSettingsIcon from '@mui/icons-material/AdminPanelSettings';
import DashboardIcon from '@mui/icons-material/Dashboard';
import FactCheckIcon from '@mui/icons-material/FactCheck';
import InventoryIcon from '@mui/icons-material/Inventory2';
import LinkIcon from '@mui/icons-material/Link';
import PersonIcon from '@mui/icons-material/Person';
import ReceiptLongIcon from '@mui/icons-material/ReceiptLong';
import StorefrontIcon from '@mui/icons-material/Storefront';
import SyncIcon from '@mui/icons-material/Sync';
import type { ElementType } from 'react';

export interface NavItem {
  to: string;
  labelKey: string;
  icon: ElementType;
  /** permission required to see the item (undefined = every admin) */
  perm?: string;
  end?: boolean;
}

export const NAV_ITEMS: NavItem[] = [
  { to: '/admin', labelKey: 'nav.dashboard', icon: DashboardIcon, end: true },
  { to: '/admin/orders', labelKey: 'nav.orders', icon: ReceiptLongIcon, perm: 'orders.read' },
  { to: '/admin/links', labelKey: 'nav.links', icon: LinkIcon, perm: 'links.read' },
  { to: '/admin/clients', labelKey: 'nav.clients', icon: StorefrontIcon, perm: 'clients.read' },
  { to: '/admin/products', labelKey: 'nav.products', icon: InventoryIcon, perm: 'products.read' },
  { to: '/admin/sync', labelKey: 'nav.sync', icon: SyncIcon, perm: 'sync.read' },
  {
    to: '/admin/users',
    labelKey: 'nav.users',
    icon: AdminPanelSettingsIcon,
    perm: 'admins.manage',
  },
  { to: '/admin/audit', labelKey: 'nav.audit', icon: FactCheckIcon, perm: 'audit.read' },
  { to: '/admin/profile', labelKey: 'nav.profile', icon: PersonIcon },
];

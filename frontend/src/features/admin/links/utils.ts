import type { LinkState } from '@/api/types';

export function stateColor(state: LinkState): 'success' | 'default' | 'warning' | 'error' | 'info' {
  switch (state) {
    case 'active':
      return 'success';
    case 'scheduled':
      return 'info';
    case 'inactive':
    case 'archived':
      return 'default';
    case 'expired':
    case 'exhausted':
      return 'error';
  }
}

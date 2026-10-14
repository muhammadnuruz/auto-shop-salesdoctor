import { useQuery, type QueryClient } from '@tanstack/react-query';
import { authApi, refsApi } from '@/api/admin';
import { qk } from '@/api/keys';
import { useTranslation } from 'react-i18next';
import type { AdminMe, AdminUser, RefCountItem, RefGroupName, RefItem } from '@/api/types';

export const adminMeQuery = () => ({
  queryKey: qk.admin.me(),
  queryFn: ({ signal }: { signal: AbortSignal }) => authApi.me(signal),
  staleTime: 60_000,
});

export const useAdminMe = () => useQuery(adminMeQuery());

export const refreshAdminMe = (qc: QueryClient) =>
  qc.fetchQuery({ ...adminMeQuery(), staleTime: 0 });

export const setAdminMe = (qc: QueryClient, user: AdminUser, csrf: string): void => {
  const next: AdminMe = { user, csrf_token: csrf };
  qc.setQueryData<AdminMe | null>(qk.admin.me(), next);
};

export interface RefOption {
  id: string;
  label: string;
  active: boolean;
  /** label already includes the id (count groups) */
  full?: boolean;
}

const COUNT_GROUPS: readonly RefGroupName[] = ['territories', 'client_categories'];

/**
 * Single place that loads SD reference data for link forms. If the endpoint is missing or fails,
 * `available` is false and forms degrade to free-text ID entry. The endpoint returns only active
 * rows as `{id, name}`; territories / client categories come as `{id, clients}` (names are not
 * mirrored in SD), rendered "id (N clients)".
 */
export function useRefs() {
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: qk.admin.refs(),
    queryFn: ({ signal }) => refsApi.get(signal),
    staleTime: 5 * 60_000,
    retry: false,
  });
  const data = q.data ?? null;
  const options = (group: RefGroupName): RefOption[] | undefined => {
    const items = data?.[group];
    if (!items) return undefined;
    if (COUNT_GROUPS.includes(group)) {
      return (items as RefCountItem[]).map((i) => ({
        id: String(i.id),
        label: t('refs.idWithClients', { id: i.id, count: i.clients }),
        active: true,
        full: true,
      }));
    }
    return (items as RefItem[]).map((i) => ({
      id: String(i.id),
      label: i.name ?? String(i.id),
      active: true,
    }));
  };
  /** Named group as a lookup (used by order forms: id -> label). */
  const named = (group: Exclude<RefGroupName, 'territories' | 'client_categories'>): RefItem[] =>
    data?.[group] ?? [];
  return { isLoading: q.isLoading, available: data !== null, options, named };
}

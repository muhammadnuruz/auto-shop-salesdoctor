import { useQuery, type QueryClient } from '@tanstack/react-query';
import { shopApi } from '@/api/shop';
import { qk } from '@/api/keys';

export const shopMeQuery = () => ({
  queryKey: qk.shop.me(),
  queryFn: ({ signal }: { signal: AbortSignal }) => shopApi.me(signal),
  staleTime: 60_000,
});

export const useShopMe = () => useQuery(shopMeQuery());

/** Force-load /me after login so guards see the fresh session before navigation. */
export const refreshShopMe = (qc: QueryClient) => qc.fetchQuery({ ...shopMeQuery(), staleTime: 0 });

export const useLinkInfo = (slug: string) =>
  useQuery({
    queryKey: qk.shop.link(slug),
    queryFn: ({ signal }) => shopApi.link(slug, signal),
  });

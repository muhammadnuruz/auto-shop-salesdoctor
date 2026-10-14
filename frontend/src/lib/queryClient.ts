import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query';
import i18n from '@/i18n';
import { ApiError } from '@/api/http';
import { qk } from '@/api/keys';
import { describeError, shouldRetry } from './errors';
import { toast } from './toast';

declare module '@tanstack/react-query' {
  interface Register {
    mutationMeta: { silent?: boolean };
    queryMeta: { silent?: boolean };
  }
}

export function createQueryClient(): QueryClient {
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({
      onError: (err, query) => {
        handleAuthLoss(client, err);
        // Only toast background refetch failures; first-load failures render an error state.
        if (
          query.state.data !== undefined &&
          !query.meta?.silent &&
          !(err instanceof ApiError && err.status === 401)
        ) {
          toast(describeError(err, i18n.t.bind(i18n)), 'error');
        }
      },
    }),
    mutationCache: new MutationCache({
      onError: (err, _vars, _ctx, mutation) => {
        handleAuthLoss(client, err);
        if (mutation.meta?.silent || (err instanceof ApiError && err.status === 401)) return;
        toast(describeError(err, i18n.t.bind(i18n)), 'error');
      },
    }),
    defaultOptions: {
      queries: {
        retry: shouldRetry,
        staleTime: 15_000,
        refetchOnWindowFocus: false,
      },
      mutations: { retry: false },
    },
  });
  return client;
}

/** A 401 on an authenticated call means the session is gone: drop it so route guards redirect. */
function handleAuthLoss(client: QueryClient, err: unknown): void {
  if (!(err instanceof ApiError) || err.status !== 401 || err.isAuthAttempt) return;
  if (err.realm === 'admin') client.setQueryData(qk.admin.me(), null);
  else if (err.realm === 'shop') client.setQueryData(qk.shop.me(), null);
}

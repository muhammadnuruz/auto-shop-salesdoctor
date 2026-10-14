import { render } from '@testing-library/react';
import { RouterProvider } from 'react-router-dom';
import { Providers } from '@/Providers';
import { createQueryClient } from '@/lib/queryClient';
import { createTestRouter } from '@/router';

/** Render the real route tree at `path`, with retries disabled for fast, deterministic tests. */
export function renderApp(path: string) {
  const client = createQueryClient();
  client.setDefaultOptions({
    queries: { retry: false, staleTime: 0, refetchOnWindowFocus: false },
    mutations: { retry: false },
  });
  const router = createTestRouter([path]);
  const utils = render(
    <Providers client={client}>
      <RouterProvider router={router} />
    </Providers>,
  );
  return { ...utils, router, client };
}

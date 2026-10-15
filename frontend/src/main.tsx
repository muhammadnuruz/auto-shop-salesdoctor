import '@fontsource/roboto/400.css';
import '@fontsource/roboto/500.css';
import '@fontsource/roboto/700.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router-dom';
import { setCsrfRefresher } from '@/api/http';
import { authApi } from '@/api/admin';
import { shopApi } from '@/api/shop';
import { installGlobalErrorReporting } from '@/lib/errorReporting';
import { createQueryClient } from '@/lib/queryClient';
import { Providers } from './Providers';
import { createAppRouter } from './router';
import './i18n';

// A stale CSRF token is refreshed from /me and the request retried once (see api/http.ts).
setCsrfRefresher(async (realm) => {
  if (realm === 'admin') await authApi.me();
  else await shopApi.me();
});

installGlobalErrorReporting();

const queryClient = createQueryClient();
const router = createAppRouter();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Providers client={queryClient}>
      <RouterProvider router={router} />
    </Providers>
  </StrictMode>,
);

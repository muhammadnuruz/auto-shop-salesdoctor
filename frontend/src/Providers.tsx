import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { SnackbarProvider } from 'notistack';
import type { ReactNode } from 'react';
import { I18nextProvider } from 'react-i18next';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { ToastBridge } from '@/components/ToastBridge';
import i18n from '@/i18n';
import { ColorModeProvider } from '@/theme/ColorMode';

export function Providers({ client, children }: { client: QueryClient; children: ReactNode }) {
  return (
    <I18nextProvider i18n={i18n}>
      <ColorModeProvider>
        <ErrorBoundary>
          <QueryClientProvider client={client}>
            <SnackbarProvider
              maxSnack={3}
              autoHideDuration={4000}
              anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
            >
              <ToastBridge />
              {children}
            </SnackbarProvider>
          </QueryClientProvider>
        </ErrorBoundary>
      </ColorModeProvider>
    </I18nextProvider>
  );
}

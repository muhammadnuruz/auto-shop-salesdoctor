import type { OptionsObject, SnackbarKey, SnackbarMessage } from 'notistack';

type Enqueue = (message: SnackbarMessage, options?: OptionsObject) => SnackbarKey;
let enqueue: Enqueue | null = null;

/** Lets non-React code (query cache handlers) raise toasts; wired by <ToastBridge/>. */
export const setToastEnqueue = (fn: Enqueue | null): void => {
  enqueue = fn;
};
export const toast = (
  message: string,
  variant: 'error' | 'success' | 'info' | 'warning' = 'info',
) => enqueue?.(message, { variant });

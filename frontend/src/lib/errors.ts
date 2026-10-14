import type { TFunction } from 'i18next';
import { ApiError } from '@/api/http';

/** Human message for an arbitrary error, localised. Never leaks stack traces. */
export function describeError(err: unknown, t: TFunction): string {
  if (!(err instanceof ApiError)) return t('errors.unknown');
  switch (err.status) {
    case 0:
      return t('errors.network');
    case 401:
      return t('errors.unauthorized');
    case 403:
      return t('errors.forbidden');
    case 404:
      return t('errors.notFound');
    case 429:
      return err.retryAfter != null
        ? t('errors.tooMany', { seconds: err.retryAfter })
        : t('errors.tooManyNoTime');
    default:
      if (err.status >= 500) return t('errors.server');
      // 4xx with a readable server message (admin-facing text is fine to show)
      return err.issues.map((i) => i.message).join('; ') || t('errors.unknown');
  }
}

export const isStatus = (err: unknown, status: number): boolean =>
  err instanceof ApiError && err.status === status;

/** Retry network failures and 5xx a couple of times; never retry 4xx. */
export function shouldRetry(failureCount: number, err: unknown): boolean {
  if (err instanceof ApiError && err.status >= 400 && err.status < 500) return false;
  return failureCount < 2;
}

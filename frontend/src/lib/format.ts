import dayjs from 'dayjs';

/** "1234567.00" -> "1 234 567" (regular spaces, no fraction unless it is non-zero). */
export function formatAmount(value: string | number | null | undefined): string {
  const n = typeof value === 'string' ? Number(value) : (value ?? 0);
  if (!Number.isFinite(n)) return '0';
  const hasFraction = Math.abs(n % 1) > 0.004;
  const fixed = Math.abs(n).toFixed(hasFraction ? 2 : 0);
  const [int = '0', frac] = fixed.split('.');
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  return `${n < 0 ? '-' : ''}${grouped}${frac ? `.${frac}` : ''}`;
}

export const formatMoney = (value: string | number | null | undefined, unit: string): string =>
  `${formatAmount(value)} ${unit}`;

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = dayjs(iso);
  return d.isValid() ? d.format('DD.MM.YYYY HH:mm') : '—';
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  return s < 60 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

/** ISO instant -> value for <input type="datetime-local"> in the browser's zone. */
export function isoToLocalInput(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = dayjs(iso);
  return d.isValid() ? d.format('YYYY-MM-DDTHH:mm') : '';
}

/** <input type="datetime-local"> value -> ISO instant with offset (backend needs aware datetimes). */
export function localInputToIso(value: string): string | null {
  if (!value) return null;
  const d = dayjs(value);
  return d.isValid() ? d.toISOString() : null;
}

/** `2026-10-16 09:46:06` -> `16.10.2026 09:46` */
export function formatInvoiceDate(value: string | null): string {
  if (!value) return '';
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/.exec(value);
  if (!m) return value;
  const day = `${m[3]}.${m[2]}.${m[1]}`;
  return m[4] ? `${day} ${m[4]}:${m[5]}` : day;
}

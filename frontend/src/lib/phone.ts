/** Uzbek phone helpers: the UI shows `+998 XX XXX XX XX`; only the 9 national digits are state. */

export const PHONE_DIGITS = 9;

/** Extract up to 9 national digits from anything typed or pasted (drops +998 / 998 / 8 prefix). */
export function nationalDigits(input: string): string {
  let d = input.replace(/\D/g, '');
  if (d.startsWith('998') && d.length > PHONE_DIGITS) d = d.slice(3);
  else if (d.startsWith('998') && input.trim().startsWith('+')) d = d.slice(3);
  return d.slice(0, PHONE_DIGITS);
}

/** "901234567" -> "90 123 45 67" (progressive). */
export function formatNational(digits: string): string {
  const d = digits.slice(0, PHONE_DIGITS);
  const parts = [d.slice(0, 2), d.slice(2, 5), d.slice(5, 7), d.slice(7, 9)].filter(Boolean);
  return parts.join(' ');
}

export const toE164 = (digits: string): string => `+998${digits}`;

/** phone_key (9 digits) -> "+998 90 123 45 67" */
export const formatPhoneKey = (key: string): string => `+998 ${formatNational(key)}`;

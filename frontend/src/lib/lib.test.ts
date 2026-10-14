import { describe, expect, it } from 'vitest';
import ru from '@/i18n/ru';
import uz from '@/i18n/uz';
import { formatAmount, formatMoney } from './format';
import { formatNational, nationalDigits } from './phone';
import { slugIsGuessable } from './slug';

const leaves = (o: object, prefix = ''): string[] =>
  Object.entries(o).flatMap(([k, v]) =>
    typeof v === 'object' && v !== null ? leaves(v, `${prefix}${k}.`) : [`${prefix}${k}`],
  );

describe('formatting', () => {
  it('formats UZS amounts with space grouping', () => {
    expect(formatAmount('1234567.00')).toBe('1 234 567');
    expect(formatMoney(1234567, "so'm")).toBe("1 234 567 so'm");
    expect(formatAmount('-5000')).toBe('-5 000');
    expect(formatAmount('1500.5')).toBe('1 500.50');
    expect(formatAmount(null)).toBe('0');
  });
});

describe('phone mask', () => {
  it('formats progressively as XX XXX XX XX', () => {
    expect(formatNational('9')).toBe('9');
    expect(formatNational('90123')).toBe('90 123');
    expect(formatNational('901234567')).toBe('90 123 45 67');
  });
  it('strips +998 / 998 prefixes and non-digits, caps at 9 digits', () => {
    expect(nationalDigits('+998 90 123 45 67')).toBe('901234567');
    expect(nationalDigits('998901234567')).toBe('901234567');
    expect(nationalDigits('90-123-45-67-99')).toBe('901234567');
  });
});

describe('slug heuristic', () => {
  it('flags guessable slugs only', () => {
    expect(slugIsGuessable('123456')).toBe(true);
    expect(slugIsGuessable('aaaaaa')).toBe(true);
    expect(slugIsGuessable('shop-test')).toBe(true);
    expect(slugIsGuessable('k8fq2xz9')).toBe(false);
  });
});

describe('i18n', () => {
  it('uz has exactly the same keys as ru', () => {
    expect(leaves(uz).sort()).toEqual(leaves(ru).sort());
  });
});

/** Same heuristic as the backend (slug_is_guessable): repeated/numeric/common-word slugs. */
const GUESSABLE_WORDS = new Set([
  'shop',
  'test',
  'dokon',
  'magazin',
  'admin',
  'link',
  'demo',
  'store',
  'order',
  'main',
  'new',
]);

export function slugIsGuessable(slug: string): boolean {
  const body = slug.replaceAll('-', '');
  if (!body) return false;
  if (new Set(body).size <= 3 || /^\d+$/.test(body)) return true;
  const tokens = slug.split('-').filter((t) => t && !/^\d+$/.test(t));
  return tokens.length > 0 && tokens.every((t) => GUESSABLE_WORDS.has(t));
}

export const linkUrl = (slug: string): string => `${window.location.origin}/s/${slug}`;

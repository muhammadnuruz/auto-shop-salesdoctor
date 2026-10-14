import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import ru from './ru';
import uz from './uz';

export const LANGS = ['ru', 'uz'] as const;
export type Lang = (typeof LANGS)[number];
export const LANG_STORAGE_KEY = 'autoshop.lang';
export const LANG_LABELS: Record<Lang, string> = { ru: 'Русский', uz: "O'zbekcha" };

function initialLang(): Lang {
  try {
    const saved = localStorage.getItem(LANG_STORAGE_KEY);
    if (saved === 'ru' || saved === 'uz') return saved;
  } catch {
    /* storage unavailable */
  }
  return 'ru';
}

void i18n.use(initReactI18next).init({
  resources: { ru: { translation: ru }, uz: { translation: uz } },
  lng: initialLang(),
  fallbackLng: 'ru',
  interpolation: { escapeValue: false },
  returnNull: false,
});

export function setLanguage(lang: Lang): void {
  try {
    localStorage.setItem(LANG_STORAGE_KEY, lang);
  } catch {
    /* ignore */
  }
  void i18n.changeLanguage(lang);
  document.documentElement.lang = lang === 'uz' ? 'uz-Latn' : 'ru';
}

document.documentElement.lang = i18n.language === 'uz' ? 'uz-Latn' : 'ru';

export default i18n;

import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import en from './locales/en';
import zh from './locales/zh';

export const LANGUAGE_STORAGE_KEY = 'pipedeck.language';
export const SUPPORTED_LANGUAGES = ['zh', 'en'] as const;
export type Language = (typeof SUPPORTED_LANGUAGES)[number];

function detectInitialLanguage(): Language {
  if (typeof window === 'undefined') return 'zh';
  const saved = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
  return saved === 'zh' || saved === 'en' ? saved : 'zh';
}

void i18n.use(initReactI18next).init({
  resources: {
    zh: { translation: zh },
    en: { translation: en },
  },
  lng: detectInitialLanguage(),
  fallbackLng: 'zh',
  interpolation: { escapeValue: false },
  returnNull: false,
});

export function setLanguage(language: Language): void {
  void i18n.changeLanguage(language);
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
  }
}

export default i18n;

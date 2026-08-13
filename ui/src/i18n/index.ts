/**
 * i18next bootstrap — imported for its side effect from `main.tsx` before the app
 * renders. Resources are bundled (no async backend), so `useTranslation()` works
 * synchronously everywhere without a Suspense boundary or an `<I18nextProvider>`.
 *
 * The active language mirrors to the document's `lang` attribute and the shared
 * date formatter (`lib/format`). Persistence is deliberately NOT done here — it's
 * tied to the explicit `setLanguage` action, so a browser-detected default is
 * never frozen into storage on first load (which would stop it tracking the
 * browser on later visits).
 */
import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import { setDateLocale } from '../lib/format'
import { DEFAULT_LANG, SUPPORTED, detectInitialLang, persistLang, type Lang } from './detect'
import en from './locales/en/translation.json'
import ptBR from './locales/pt-BR/translation.json'

/** Keep the document + date formatter aligned with the active language. */
function applyLang(lang: string): void {
  document.documentElement.lang = lang
  setDateLocale(lang)
}

const initial = detectInitialLang()

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, 'pt-BR': { translation: ptBR } },
  lng: initial,
  fallbackLng: DEFAULT_LANG,
  supportedLngs: [...SUPPORTED],
  interpolation: { escapeValue: false }, // React already escapes rendered values
  react: { useSuspense: false },
})

applyLang(initial)
i18n.on('languageChanged', applyLang)

/** Switch + remember the console language — the only path that persists a choice. */
export function setLanguage(lang: Lang): void {
  persistLang(lang)
  void i18n.changeLanguage(lang)
}

export default i18n

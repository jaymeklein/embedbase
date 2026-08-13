/**
 * Type-safe translation keys: augment i18next's `CustomTypeOptions` with the `en`
 * catalog as the source of truth, so every `t('…')` call is checked against the
 * real key set at compile time. Catalog parity between locales is enforced
 * separately in `parity.ts`.
 */
import 'i18next'
import type en from './locales/en/translation.json'

declare module 'i18next' {
  interface CustomTypeOptions {
    defaultNS: 'translation'
    resources: { translation: typeof en }
  }
}

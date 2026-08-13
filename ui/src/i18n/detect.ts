/**
 * Language detection + persistence for the console UI, decoupled from React and
 * i18next so boot code can resolve a language synchronously (mirrors the storage
 * try/catch shape of `api/tokenStore.ts`).
 *
 * Precedence: an explicit saved choice → the browser's languages → `en`.
 */

export const SUPPORTED = ['en', 'pt-BR'] as const
export type Lang = (typeof SUPPORTED)[number]
export const DEFAULT_LANG: Lang = 'en'

const STORAGE_KEY = 'embedbase.lang'

/** Map a raw BCP-47 tag (e.g. `pt`, `pt-BR`, `pt-PT`, `en-US`) to a supported
 *  language, or `null` when it's neither Portuguese nor English. */
export function normalize(raw: string | null | undefined): Lang | null {
  if (!raw) return null
  const tag = raw.toLowerCase()
  if (tag.startsWith('pt')) return 'pt-BR'
  if (tag.startsWith('en')) return 'en'
  return null
}

/** The persisted explicit choice, or `null` (also `null` when storage is off). */
export function readStoredLang(): Lang | null {
  try {
    return normalize(window.localStorage.getItem(STORAGE_KEY))
  } catch {
    // Private-mode / storage-disabled: fall back to detection.
    return null
  }
}

/** Persist the explicit choice; silently no-ops when storage is unavailable. */
export function persistLang(lang: Lang): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, lang)
  } catch {
    // Storage unavailable — the in-memory i18next language still applies.
  }
}

/** Resolve the language to boot with: saved choice → browser scan → `en`. */
export function detectInitialLang(): Lang {
  const saved = readStoredLang()
  if (saved) return saved
  const candidates =
    navigator.languages && navigator.languages.length > 0
      ? navigator.languages
      : [navigator.language]
  for (const candidate of candidates) {
    const match = normalize(candidate)
    if (match) return match
  }
  return DEFAULT_LANG
}

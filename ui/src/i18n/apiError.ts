import type { TFunction } from 'i18next'
import { ApiError } from '../api/client'

type ErrorKey =
  | 'errors.network'
  | 'errors.sessionExpired'
  | 'errors.accountDeactivated'
  | 'errors.adminRequired'
  | 'errors.configNotLoaded'
  | 'errors.passwordChangeRequired'

// Curated map of known, *static* backend `detail` strings (and the sentinels the
// API client throws) → localized keys. Interpolated / free-text details (anything
// embedding `{exc}`) are deliberately left out — they fall through to the raw
// message below rather than being string-matched.
const DETAIL_KEYS: Record<string, ErrorKey> = {
  'Your account has been deactivated.': 'errors.accountDeactivated',
  'User is inactive': 'errors.accountDeactivated',
  'Administrator access required': 'errors.adminRequired',
  'Config not loaded yet': 'errors.configNotLoaded',
  'Password change required': 'errors.passwordChangeRequired',
}

/**
 * Map an error thrown by the API client to a user-facing, localized message.
 * Status rules come first (prose-independent and robust), then the curated detail
 * map; otherwise the raw message passes through so nothing user-visible is lost.
 */
export function apiErrorMessage(err: unknown, t: TFunction): string {
  if (!(err instanceof ApiError)) return t('errors.network')
  if (err.status === 401) return t('errors.sessionExpired')
  const key = DETAIL_KEYS[err.message]
  return key ? t(key) : err.message
}

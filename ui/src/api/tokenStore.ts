/**
 * Master-key holder, decoupled from React so the fetch client can read it
 * without importing component state.
 *
 * Security trade-off (CAP-style note): the key is persisted in `localStorage`
 * so a reload keeps the operator unlocked. That exposes it to any XSS on this
 * origin. Accepted for a single-operator, local-first admin tool — the key is
 * never logged, never sent anywhere but the same-origin `/api` proxy, and the
 * operator can Lock to clear it. See [[D5.2 - API Client & Auth]].
 */

const STORAGE_KEY = 'embedbase.masterKey'

let currentKey: string | null = readStored()
let onUnauthorized: (() => void) | null = null

function readStored(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY)
  } catch {
    // Private-mode / storage-disabled: fall back to in-memory only.
    return null
  }
}

/** The current master key, or `null` when locked. */
export function getMasterKey(): string | null {
  return currentKey
}

/** Persist and activate a master key. */
export function setMasterKey(key: string): void {
  currentKey = key
  try {
    window.localStorage.setItem(STORAGE_KEY, key)
  } catch {
    // Storage unavailable — keep the in-memory key for this session only.
  }
}

/** Forget the master key everywhere (Lock). */
export function clearMasterKey(): void {
  currentKey = null
  try {
    window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Nothing persisted to clear.
  }
}

/**
 * Register the callback fired when the API returns 401 (e.g. the key was
 * revoked mid-session). The auth provider wires this to its `lock()`.
 */
export function registerUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn
}

/** Invoked by the API client on any 401 response. */
export function notifyUnauthorized(): void {
  onUnauthorized?.()
}

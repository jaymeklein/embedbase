/**
 * Bearer-credential holder, decoupled from React so the fetch client and the
 * WebSocket can read it without importing component state.
 *
 * Two credentials, both sent as `Authorization: Bearer`:
 *  - a **session token** — the JWT from `POST /auth/login` (a user is signed in);
 *  - the **master key** — the bootstrap/break-glass admin credential.
 * `getToken()` prefers the session; the master key is the fallback. Sign-out
 * (`clearAll`) forgets both.
 *
 * Security trade-off (CAP-style note): both are persisted in `localStorage` so a
 * reload keeps the operator signed in. That exposes them to any XSS on this
 * origin. Accepted for a local-first admin tool — neither is ever logged nor sent
 * anywhere but the same-origin `/api` proxy, and sign-out clears them.
 */

const MASTER_STORAGE_KEY = 'embedbase.masterKey'
const SESSION_STORAGE_KEY = 'embedbase.session'

let masterKey: string | null = readStored(MASTER_STORAGE_KEY)
let sessionToken: string | null = readStored(SESSION_STORAGE_KEY)
let onUnauthorized: (() => void) | null = null

function readStored(storageKey: string): string | null {
  try {
    return window.localStorage.getItem(storageKey)
  } catch {
    // Private-mode / storage-disabled: fall back to in-memory only.
    return null
  }
}

function persist(storageKey: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(storageKey)
    else window.localStorage.setItem(storageKey, value)
  } catch {
    // Storage unavailable — the in-memory value still applies for this session.
  }
}

/** The active bearer credential — a login session takes precedence over the master key. */
export function getToken(): string | null {
  return sessionToken ?? masterKey
}

/** The current master key, or `null`. */
export function getMasterKey(): string | null {
  return masterKey
}

/** Persist + activate the master key (bootstrap unlock). */
export function setMasterKey(key: string): void {
  masterKey = key
  persist(MASTER_STORAGE_KEY, key)
}

/** Forget the master key. */
export function clearMasterKey(): void {
  masterKey = null
  persist(MASTER_STORAGE_KEY, null)
}

/** The current login-session JWT, or `null`. */
export function getSessionToken(): string | null {
  return sessionToken
}

/** Persist + activate a login-session JWT. */
export function setSessionToken(token: string): void {
  sessionToken = token
  persist(SESSION_STORAGE_KEY, token)
}

/** Forget the login session. */
export function clearSessionToken(): void {
  sessionToken = null
  persist(SESSION_STORAGE_KEY, null)
}

/** Forget every credential (full sign-out). */
export function clearAll(): void {
  clearSessionToken()
  clearMasterKey()
}

/**
 * Register the callback fired when the API returns 401 (e.g. an expired session or
 * a revoked key). The auth provider wires this to its sign-out.
 */
export function registerUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn
}

/** Invoked by the API client on any 401 response. */
export function notifyUnauthorized(): void {
  onUnauthorized?.()
}

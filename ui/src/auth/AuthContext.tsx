import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import {
  clearMasterKey,
  getMasterKey,
  registerUnauthorizedHandler,
  setMasterKey,
} from '../api/tokenStore'

interface AuthValue {
  /** True when a master key is held (verified on unlock, or hydrated on load). */
  isUnlocked: boolean
  /** Store + verify a key against an authed endpoint. Throws if rejected. */
  unlock: (key: string) => Promise<void>
  /** Forget the key and drop any cached authed data. */
  lock: () => void
}

const AuthContext = createContext<AuthValue | null>(null)

/**
 * Owns the single-operator unlock state. The raw key lives in the decoupled
 * token store (`api/tokenStore`); this provider exposes verify/lock actions and
 * bridges a global 401 back to `lock()`.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  // Hydrate optimistically: a stored key means "unlocked". If it is actually
  // stale, the first authed query 401s and the handler below locks us out.
  const [isUnlocked, setUnlocked] = useState(() => getMasterKey() !== null)

  const lock = useCallback(() => {
    clearMasterKey()
    setUnlocked(false)
    queryClient.clear()
  }, [queryClient])

  const unlock = useCallback(async (key: string) => {
    setMasterKey(key)
    try {
      await api.listWorkspaces()
    } catch (err) {
      clearMasterKey()
      throw err
    }
    setUnlocked(true)
  }, [])

  useEffect(() => {
    registerUnauthorizedHandler(lock)
  }, [lock])

  const value = useMemo<AuthValue>(
    () => ({ isUnlocked, unlock, lock }),
    [isUnlocked, unlock, lock],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

/** Access the auth actions. Throws if used outside {@link AuthProvider}. */
export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}

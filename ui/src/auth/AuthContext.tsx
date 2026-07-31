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
import type { User } from '../api/types'
import {
  clearAll,
  getSessionToken,
  getToken,
  registerUnauthorizedHandler,
  setMasterKey,
  setSessionToken,
} from '../api/tokenStore'

interface AuthValue {
  /** True when a credential is held — a login session or the master key. */
  isAuthed: boolean
  /** True for an admin session or the master key (the full console). */
  isAdmin: boolean
  /** True when the user (or master key) may create workspaces. */
  canCreateWorkspaces: boolean
  /** True when the user (or master key) may manage tags (the `manage_tags` capability). */
  canManageTags: boolean
  /** True when the signed-in user must set a new password before using the app. */
  mustChangePassword: boolean
  /** True while the initial `/auth/me` for a stored session is in flight (gates the
   *  app so a reload never flashes the wrong role / must-change state). */
  hydrating: boolean
  /** The signed-in user, or `null` for a master-key bootstrap session. */
  currentUser: User | null
  /** Sign in with username + password. Throws if rejected. */
  login: (username: string, password: string) => Promise<void>
  /** Bootstrap unlock with the master key (break-glass admin). Throws if rejected. */
  unlock: (masterKey: string) => Promise<void>
  /** Set a new password (first-login or voluntary), then continue. Throws if rejected. */
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>
  /** Forget every credential + cached data (sign out). */
  logout: () => void
}

const AuthContext = createContext<AuthValue | null>(null)

/**
 * Owns the console auth state. Credentials live in the decoupled token store
 * (`api/tokenStore`); this provider exposes login/unlock/change/logout, tracks the
 * current user + role, and bridges a global 401 back to `logout()`.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [isAuthed, setIsAuthed] = useState(() => getToken() !== null)
  const [currentUser, setCurrentUser] = useState<User | null>(null)
  // A master-key-only session has no user but is full-admin. A login session's
  // admin/must-change come from /auth/me once hydrated.
  const [isAdmin, setIsAdmin] = useState(() => getToken() !== null && getSessionToken() === null)
  // Master-key sessions may create workspaces; a login session's capability comes from /auth/me.
  const [canCreateWorkspaces, setCanCreateWorkspaces] = useState(
    () => getToken() !== null && getSessionToken() === null,
  )
  // Master-key sessions may manage tags; a login session's capability comes from /auth/me.
  const [canManageTags, setCanManageTags] = useState(
    () => getToken() !== null && getSessionToken() === null,
  )
  const [mustChangePassword, setMustChangePassword] = useState(false)
  // A stored login session must be verified via /auth/me before we render the app,
  // or a reload would flash the wrong role / must-change state.
  const [hydrating, setHydrating] = useState(() => getSessionToken() !== null)

  const logout = useCallback(() => {
    clearAll()
    setIsAuthed(false)
    setIsAdmin(false)
    setCanCreateWorkspaces(false)
    setCanManageTags(false)
    setMustChangePassword(false)
    setCurrentUser(null)
    queryClient.clear()
  }, [queryClient])

  const applyUser = useCallback((user: User) => {
    setCurrentUser(user)
    setIsAdmin(user.is_admin)
    setCanCreateWorkspaces(user.can_create_workspaces ?? false)
    setCanManageTags(user.can_manage_tags ?? false)
    setMustChangePassword(user.must_change_password)
    setIsAuthed(true)
  }, [])

  const login = useCallback(
    async (username: string, password: string) => {
      const res = await api.login(username, password)
      setSessionToken(res.access_token)
      try {
        applyUser(await api.me())
      } catch (err) {
        clearAll()
        throw err
      }
    },
    [applyUser],
  )

  const unlock = useCallback(async (masterKey: string) => {
    setMasterKey(masterKey)
    try {
      await api.listWorkspaces() // probe: the master key is valid iff this succeeds
    } catch (err) {
      clearAll()
      throw err
    }
    setCurrentUser(null)
    setIsAdmin(true)
    setCanCreateWorkspaces(true)
    setCanManageTags(true)
    setMustChangePassword(false)
    setIsAuthed(true)
  }, [])

  const changePassword = useCallback(
    async (currentPassword: string, newPassword: string) => {
      const res = await api.changePassword(currentPassword, newPassword)
      // The password is now rotated and this token is fresh — unblock the app from
      // the response so a follow-up /auth/me hiccup can't strand the user on the
      // change screen. Identity refresh is then best-effort.
      setSessionToken(res.access_token)
      setMustChangePassword(res.must_change_password)
      setIsAuthed(true)
      try {
        applyUser(await api.me())
      } catch {
        // Non-fatal: a later request re-hydrates or trips the 401 handler.
      }
    },
    [applyUser],
  )

  // Hydrate identity for a stored login session on load; a 401 (expired/reset) trips
  // the unauthorized handler → logout. Guard against a late resolve after unmount or
  // sign-out (the token is gone → don't resurrect the session).
  useEffect(() => {
    if (getSessionToken() === null) return
    let active = true
    api
      .me()
      .then((user) => {
        if (active && getSessionToken() !== null) applyUser(user)
      })
      .catch(() => {})
      .finally(() => {
        if (active) setHydrating(false)
      })
    return () => {
      active = false
    }
  }, [applyUser])

  useEffect(() => {
    registerUnauthorizedHandler(logout)
  }, [logout])

  const value = useMemo<AuthValue>(
    () => ({
      isAuthed,
      isAdmin,
      canCreateWorkspaces,
      canManageTags,
      mustChangePassword,
      hydrating,
      currentUser,
      login,
      unlock,
      changePassword,
      logout,
    }),
    [
      isAuthed,
      isAdmin,
      canCreateWorkspaces,
      canManageTags,
      mustChangePassword,
      hydrating,
      currentUser,
      login,
      unlock,
      changePassword,
      logout,
    ],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

/** Access the auth state + actions. Throws if used outside {@link AuthProvider}. */
export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}

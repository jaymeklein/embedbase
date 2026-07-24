import { KeyRound } from 'lucide-react'
import { ChangePasswordForm } from './ChangePasswordForm'
import { useAuth } from './AuthContext'

/**
 * Forced first-login gate: a must-change user lands here and can do nothing else
 * until they set a new password (the backend rejects every other route). On success
 * `changePassword` clears the flag and the app renders.
 */
export function ChangePasswordScreen() {
  const { currentUser, logout } = useAuth()
  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-4">
      <div className="w-full max-w-sm animate-fade-in">
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-card bg-accent-weak text-accent">
            <KeyRound className="h-6 w-6" />
          </div>
          <h1 className="text-lg font-semibold tracking-tight text-ink">Set a new password</h1>
          <p className="mt-1 text-[13px] text-ink-muted">
            {currentUser?.username ? `Signed in as ${currentUser.username}. ` : ''}
            Choose a new password to finish signing in.
          </p>
        </div>
        <ChangePasswordForm submitLabel="Set password & continue" />
        <button
          type="button"
          onClick={logout}
          className="mt-4 w-full text-center text-xs text-ink-faint hover:text-ink-muted"
        >
          Sign out
        </button>
      </div>
    </div>
  )
}

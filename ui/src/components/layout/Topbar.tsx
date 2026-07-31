import { KeyRound, LogOut } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useAuth } from '../../auth/AuthContext'
import { ChangePasswordModal } from '../../auth/ChangePasswordModal'

/**
 * Top bar with a breadcrumb slot and the account controls: who's signed in, an
 * optional change-password action (session users only), and Sign out.
 */
export function Topbar({ children }: { children?: ReactNode }) {
  const { currentUser, isAdmin, logout } = useAuth()
  const [changing, setChanging] = useState(false)

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-surface px-6">
      <div className="flex items-center gap-2 text-[13px] text-ink-muted">{children}</div>
      <div className="flex items-center gap-2">
        <span className="text-xs text-ink-faint" title={currentUser?.email ?? undefined}>
          {currentUser ? currentUser.username : 'Master key'}
          {isAdmin ? ' · admin' : ''}
        </span>
        {currentUser && (
          <button
            type="button"
            onClick={() => setChanging(true)}
            title="Change your password"
            className="flex items-center gap-1.5 rounded-full border border-border bg-canvas px-3 py-1 text-xs font-medium text-ink-muted transition-colors hover:border-ink-faint hover:text-ink"
          >
            <KeyRound className="h-3.5 w-3.5" />
            Change password
          </button>
        )}
        <button
          type="button"
          onClick={logout}
          title="Sign out and clear your session"
          className="flex items-center gap-1.5 rounded-full border border-border bg-canvas px-3 py-1 text-xs font-medium text-ink-muted transition-colors hover:border-ink-faint hover:text-ink"
        >
          <LogOut className="h-3.5 w-3.5" />
          Sign out
        </button>
      </div>
      <ChangePasswordModal open={changing} onClose={() => setChanging(false)} />
    </header>
  )
}

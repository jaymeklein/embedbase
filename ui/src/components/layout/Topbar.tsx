import { Lock } from 'lucide-react'
import { type ReactNode } from 'react'
import { useAuth } from '../../auth/AuthContext'

/**
 * Top bar with a breadcrumb slot and a Lock action that clears the master key
 * and returns to the unlock screen ([[D5.2 - API Client & Auth]]).
 */
export function Topbar({ children }: { children?: ReactNode }) {
  const { lock } = useAuth()
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-surface px-6">
      <div className="flex items-center gap-2 text-[13px] text-ink-muted">{children}</div>
      <button
        type="button"
        onClick={lock}
        title="Lock the console and clear the master key"
        className="flex items-center gap-1.5 rounded-full border border-border bg-canvas px-3 py-1 text-xs font-medium text-ink-muted transition-colors hover:border-ink-faint hover:text-ink"
      >
        <Lock className="h-3.5 w-3.5" />
        Lock
      </button>
    </header>
  )
}

import { AlertTriangle } from 'lucide-react'
import { Button } from './Button'

/** Inline error surface for a failed query, with an optional retry action. */
export function QueryError({
  title = 'Could not load this',
  message,
  onRetry,
}: {
  title?: string
  message?: string
  onRetry?: () => void
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-card border border-dashed border-err/40 bg-surface px-6 py-10 text-center">
      <AlertTriangle className="h-7 w-7 text-err" />
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      {message && <p className="max-w-sm text-[13px] text-ink-muted">{message}</p>}
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry} className="mt-2">
          Try again
        </Button>
      )}
    </div>
  )
}

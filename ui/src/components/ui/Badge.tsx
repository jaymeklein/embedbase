import { type ReactNode } from 'react'
import { cn } from '../../lib/cn'

/** Neutral pill for counts and metadata. */
export function Badge({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border border-border bg-canvas',
        'px-2 py-0.5 text-xs font-medium text-ink-muted',
        className,
      )}
    >
      {children}
    </span>
  )
}

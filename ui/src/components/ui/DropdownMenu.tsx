import { useEffect, useRef, useState, type ReactNode } from 'react'
import { MoreVertical } from 'lucide-react'
import { cn } from '../../lib/cn'
import { Button } from './Button'

/** A single item in a {@link DropdownMenu}. `danger` tints it destructive. */
export function DropdownItem({
  icon,
  children,
  onSelect,
  danger = false,
  disabled = false,
}: {
  icon?: ReactNode
  children: ReactNode
  onSelect: () => void
  danger?: boolean
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      onClick={onSelect}
      className={cn(
        'flex w-full items-center gap-2.5 px-3 py-2 text-left text-[13px] transition-colors',
        'disabled:pointer-events-none disabled:opacity-50',
        danger ? 'text-err hover:bg-err/10' : 'text-ink hover:bg-canvas',
      )}
    >
      {icon && <span className="shrink-0 text-ink-faint">{icon}</span>}
      {children}
    </button>
  )
}

/**
 * A click-to-open menu anchored to its trigger, collapsing a row of actions into one
 * button. Closes on item select, Esc, or an outside click. Items are `<DropdownItem>`
 * children. Keep the list short — this is for row actions, not navigation.
 */
export function DropdownMenu({
  children,
  triggerAriaLabel,
  triggerIcon,
  align = 'right',
}: {
  children: ReactNode
  triggerAriaLabel: string
  triggerIcon?: ReactNode
  align?: 'left' | 'right'
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    const onPointer = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onPointer)
    return () => {
      window.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onPointer)
    }
  }, [open])

  return (
    <div ref={ref} className="relative">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={triggerAriaLabel}
        onClick={() => setOpen((v) => !v)}
        className="h-10 w-10 px-0"
      >
        {triggerIcon ?? <MoreVertical className="h-6 w-6" />}
      </Button>
      {open && (
        <div
          role="menu"
          className={cn(
            'absolute z-20 mt-1 min-w-[11rem] overflow-hidden rounded-card border border-border',
            'bg-surface py-1 shadow-overlay animate-fade-in',
            align === 'right' ? 'right-0' : 'left-0',
          )}
          onClick={() => setOpen(false)}
        >
          {children}
        </div>
      )}
    </div>
  )
}

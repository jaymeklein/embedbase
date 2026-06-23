import { X } from 'lucide-react'
import { cn } from '../../lib/cn'

const DEFAULT_COLOR = '#5B6B7A'

/**
 * A colored tag pill. Tints background/text from the tag color (or a neutral
 * fallback). Pass `onRemove` to render a detach button (used by 3b pickers).
 */
export function TagChip({
  name,
  color,
  onRemove,
  className,
}: {
  name: string
  color?: string | null
  onRemove?: () => void
  className?: string
}) {
  const c = color || DEFAULT_COLOR
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium',
        className,
      )}
      style={{ backgroundColor: `${c}1A`, color: c }}
    >
      {name}
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${name}`}
          className="rounded-full transition-opacity hover:opacity-70"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </span>
  )
}

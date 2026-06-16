import { useTags } from '../../api/hooks'
import { cn } from '../../lib/cn'

/**
 * Workspace tag toggles for filtering a list. Selecting tags ANDs them; the
 * parent applies the actual filter against each row's echoed `tags`. Renders
 * nothing when the workspace has no tags.
 *
 * ponytail: filters the already-fetched list client-side; switch to the
 * backend `?tag=` query if these lists ever grow past a single page.
 */
export function TagFilterBar({
  wsId,
  selected,
  onToggle,
}: {
  wsId: string
  selected: string[]
  onToggle: (name: string) => void
}) {
  const { data: tags } = useTags(wsId)
  if (!tags || tags.length === 0) return null

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs text-ink-faint">Filter by tag:</span>
      {tags.map((t) => {
        const on = selected.includes(t.name)
        const c = t.color || '#5B6B7A'
        return (
          <button
            key={t.id}
            type="button"
            onClick={() => onToggle(t.name)}
            aria-pressed={on}
            className={cn(
              'rounded-full px-2 py-0.5 text-xs font-medium transition-colors',
              on ? 'text-white' : 'border border-border text-ink-muted hover:text-ink',
            )}
            style={on ? { backgroundColor: c } : undefined}
          >
            {t.name}
          </button>
        )
      })}
    </div>
  )
}

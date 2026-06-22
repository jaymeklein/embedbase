import type { TagRef } from '../../api/types'
import { cn } from '../../lib/cn'

/** Distinct tags (by id, name-sorted) present across a list of tagged items.
 *  A filter should only offer tags that can actually match something on screen. */
export function collectTags(items: { tags?: TagRef[] | null }[] | undefined): TagRef[] {
  const byId = new Map<string, TagRef>()
  for (const item of items ?? []) {
    for (const tag of item.tags ?? []) byId.set(tag.id, tag)
  }
  return [...byId.values()].sort((a, b) => a.name.localeCompare(b.name))
}

/**
 * Tag toggles for filtering a list. Selecting tags ANDs them; the parent applies
 * the actual filter against each row's echoed `tags`. Renders only the tags that
 * appear on the listed items (via {@link collectTags}) — never the whole workspace
 * vocabulary, which can run to dozens of auto-generated tags. Renders nothing when
 * none are present.
 */
export function TagFilterBar({
  tags,
  selected,
  onToggle,
}: {
  tags: TagRef[]
  selected: string[]
  onToggle: (name: string) => void
}) {
  if (tags.length === 0) return null

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

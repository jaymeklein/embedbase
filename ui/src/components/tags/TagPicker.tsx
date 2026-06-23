import { useState } from 'react'
import { Check, Plus } from 'lucide-react'
import { useTags } from '../../api/hooks'
import type { TagRef } from '../../api/types'
import { Input } from '../ui'
import { cn } from '../../lib/cn'

/** Normalize a name the same way the backend does, for exact-match detection. */
function normalize(name: string): string {
  return name.trim().toLowerCase().replace(/\s+/g, ' ')
}

/**
 * Inline popover for attaching/detaching a workspace's tags to one entity.
 *
 * Lists the workspace tags (via {@link useTags}); clicking one toggles
 * assignment. A trimmed query with no exact match offers create-on-the-fly.
 * `assigned` drives the checkmarks; the parent owns the assign/unassign/create
 * mutations so the same picker serves collections and documents.
 */
export function TagPicker({
  wsId,
  assigned,
  onAssign,
  onUnassign,
  onCreate,
  busy,
}: {
  wsId: string
  assigned: TagRef[]
  onAssign: (tagId: string) => void
  onUnassign: (tagId: string) => void
  onCreate: (name: string) => void
  busy?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const { data: tags } = useTags(wsId)

  const assignedIds = new Set(assigned.map((t) => t.id))
  const q = normalize(query)
  const candidates = (tags ?? []).filter((t) => !q || t.name.includes(q))
  const exists = (tags ?? []).some((t) => t.name === q)

  const close = () => {
    setOpen(false)
    setQuery('')
  }

  return (
    <div className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1 rounded-full border border-dashed border-border px-2 py-0.5 text-xs text-ink-muted transition-colors hover:border-accent hover:text-ink"
      >
        <Plus className="h-3.5 w-3.5" />
        Tag
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={close} />
          <div className="absolute left-0 z-20 mt-1 w-56 rounded-card border border-border bg-surface p-2 shadow-overlay">
            <Input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter or create…"
              className="h-8 text-[13px]"
            />
            <div className="mt-2 max-h-48 overflow-y-auto">
              {candidates.map((t) => {
                const on = assignedIds.has(t.id)
                return (
                  <button
                    key={t.id}
                    type="button"
                    disabled={busy}
                    onClick={() => (on ? onUnassign(t.id) : onAssign(t.id))}
                    className="flex w-full items-center justify-between gap-2 rounded-control px-2 py-1.5 text-[13px] text-ink transition-colors hover:bg-canvas disabled:opacity-50"
                  >
                    <span className="flex items-center gap-2 truncate">
                      <span
                        className="h-2.5 w-2.5 shrink-0 rounded-full"
                        style={{ backgroundColor: t.color || '#5B6B7A' }}
                      />
                      <span className="truncate">{t.name}</span>
                    </span>
                    {on && <Check className="h-4 w-4 shrink-0 text-accent" />}
                  </button>
                )
              })}
              {q && !exists && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    onCreate(query)
                    setQuery('')
                  }}
                  className={cn(
                    'flex w-full items-center gap-2 rounded-control px-2 py-1.5 text-[13px]',
                    'text-accent transition-colors hover:bg-canvas disabled:opacity-50',
                  )}
                >
                  <Plus className="h-4 w-4 shrink-0" />
                  Create “{q}”
                </button>
              )}
              {candidates.length === 0 && !q && (
                <p className="px-2 py-1.5 text-xs text-ink-faint">No tags yet.</p>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

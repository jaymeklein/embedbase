import { useEffect, useState } from 'react'
import { Sparkles } from 'lucide-react'
import type { TagSuggestion } from '../../api/types'
import { Button, Modal, Spinner } from '../ui'

/**
 * Review AI-suggested tags before anything is saved. Candidates load on open;
 * the user toggles checkboxes and approves. Approval is handed to `onApply`,
 * which persists the chosen names through the normal create + assign endpoints —
 * nothing is written until then.
 */
export function SuggestTagsModal({
  open,
  onClose,
  suggestions,
  loading,
  error,
  applying,
  onApply,
}: {
  open: boolean
  onClose: () => void
  suggestions: TagSuggestion[]
  loading: boolean
  error?: string
  applying: boolean
  onApply: (names: string[]) => void
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set())

  // Pre-check every fresh batch of candidates; the user unchecks what they skip.
  useEffect(() => {
    setSelected(new Set(suggestions.map((s) => s.name)))
  }, [suggestions])

  const toggle = (name: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Suggested tags"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={applying}>
            Cancel
          </Button>
          <Button
            onClick={() => onApply([...selected])}
            disabled={applying || selected.size === 0}
          >
            {applying ? 'Applying…' : `Apply ${selected.size || ''}`.trim()}
          </Button>
        </>
      }
    >
      {loading ? (
        <div className="flex items-center gap-2 py-6 text-[13px] text-ink-muted">
          <Spinner className="h-4 w-4" />
          Analyzing content…
        </div>
      ) : error ? (
        <p className="py-4 text-[13px] text-err">{error}</p>
      ) : suggestions.length === 0 ? (
        <div className="flex items-center gap-2 py-6 text-[13px] text-ink-muted">
          <Sparkles className="h-4 w-4 text-ink-faint" />
          No suggestions for this content.
        </div>
      ) : (
        <ul className="space-y-1">
          {suggestions.map((s) => (
            <li key={s.name}>
              <label className="flex cursor-pointer items-center gap-3 rounded-control px-2 py-1.5 hover:bg-canvas">
                <input
                  type="checkbox"
                  checked={selected.has(s.name)}
                  onChange={() => toggle(s.name)}
                  className="h-4 w-4 accent-accent"
                />
                <span className="flex-1 truncate text-[13px] text-ink">{s.name}</span>
                <span className="text-xs tabular-nums text-ink-faint">
                  {Math.round(s.confidence * 100)}%
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}
    </Modal>
  )
}

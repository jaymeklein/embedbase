import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { useCollections, useWorkspaces } from '../../api/hooks'
import type { Collection, Workspace } from '../../api/types'
import { EntityIcon, QueryError, Skeleton } from '../ui'

/** Selection callbacks shared down the tree. */
interface TreeActions {
  selected: Set<string>
  onToggle: (id: string) => void
  onToggleMany: (ids: string[], select: boolean) => void
}

/**
 * Left-panel collection picker: workspaces, each expandable to its collections
 * as checkboxes, with multi-select across workspaces and a per-workspace
 * select-all toggle.
 */
export function WorkspaceTree(actions: TreeActions) {
  const { data, isLoading, isError, error, refetch } = useWorkspaces()
  if (isLoading) {
    return (
      <div className="flex flex-col gap-2">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-7 rounded-control" />
        ))}
      </div>
    )
  }
  if (isError) {
    return <QueryError title="Could not load workspaces" message={error?.message} onRetry={() => void refetch()} />
  }
  if (!data || data.length === 0) {
    return <p className="px-1 text-xs text-ink-faint">No workspaces yet.</p>
  }
  return (
    <div className="flex flex-col gap-0.5">
      {data.map((ws) => (
        <WorkspaceNode key={ws.id} ws={ws} {...actions} />
      ))}
    </div>
  )
}

/** One workspace and its collections, collapsible, with a select-all control. */
function WorkspaceNode({ ws, selected, onToggle, onToggleMany }: { ws: Workspace } & TreeActions) {
  const { data, isLoading } = useCollections(ws.id)
  const [open, setOpen] = useState(true)
  const cols = data ?? []
  const ids = cols.map((c) => c.id)
  const allSelected = ids.length > 0 && ids.every((id) => selected.has(id))

  return (
    <div>
      <div className="flex items-center gap-1.5 py-1">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="text-ink-faint transition-colors hover:text-ink"
          aria-label={open ? `Collapse ${ws.name}` : `Expand ${ws.name}`}
        >
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>
        <span style={{ color: ws.color }}>
          <EntityIcon name={ws.icon} className="h-4 w-4" />
        </span>
        <span className="flex-1 truncate text-[13px] font-medium text-ink">{ws.name}</span>
        {ids.length > 0 && (
          <button
            type="button"
            onClick={() => onToggleMany(ids, !allSelected)}
            className="text-xs text-accent hover:underline"
          >
            {allSelected ? 'Clear' : 'All'}
          </button>
        )}
      </div>
      {open && (
        <div className="ml-5 flex flex-col">
          {isLoading ? (
            <Skeleton className="ml-2 h-6 w-32 rounded-control" />
          ) : cols.length === 0 ? (
            <p className="px-2 py-1 text-xs text-ink-faint">No collections</p>
          ) : (
            cols.map((col) => (
              <CollectionCheckbox
                key={col.id}
                col={col}
                checked={selected.has(col.id)}
                onChange={() => onToggle(col.id)}
              />
            ))
          )}
        </div>
      )}
    </div>
  )
}

/** A selectable collection row with a document-count badge. */
function CollectionCheckbox({
  col,
  checked,
  onChange,
}: {
  col: Collection
  checked: boolean
  onChange: () => void
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2 rounded-control px-2 py-1.5 hover:bg-canvas">
      <input
        type="checkbox"
        checked={checked}
        onChange={onChange}
        className="h-4 w-4 shrink-0 accent-accent"
      />
      <span className="flex-1 truncate text-[13px] text-ink">{col.name}</span>
      {col.document_count !== undefined && (
        <span className="font-mono text-xs text-ink-faint">{col.document_count}</span>
      )}
    </label>
  )
}

import { useState, type MouseEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { FolderKanban, Layers, Pencil, Plus, Trash2 } from 'lucide-react'
import { useCreateWorkspace, useDeleteWorkspace, useUpdateWorkspace, useWorkspaces } from '../api/hooks'
import type { Workspace, WorkspaceUpdate } from '../api/types'
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  EntityIcon,
  QueryError,
  Skeleton,
  useToast,
} from '../components/ui'
import {
  WorkspaceFormModal,
  type WorkspaceFormValues,
} from '../components/workspaces/WorkspaceFormModal'
import { formatDate } from '../lib/format'

/** Which dialog (if any) is currently open, plus the row it acts on. */
type Dialog =
  | { kind: 'none' }
  | { kind: 'create' }
  | { kind: 'edit'; ws: Workspace }
  | { kind: 'delete'; ws: Workspace }

/** Reduce a full form submission to only the fields that actually changed. */
function changedFields(ws: Workspace, values: WorkspaceFormValues): WorkspaceUpdate {
  const body: WorkspaceUpdate = {}
  if (values.name !== ws.name) body.name = values.name
  if (values.description !== (ws.description ?? '')) body.description = values.description
  if (values.color !== ws.color) body.color = values.color
  if (values.icon !== ws.icon) body.icon = values.icon
  return body
}

/** Workspaces index: list, create, edit, and delete the top-level containers. */
export default function Workspaces() {
  const { data, isLoading, isError, error, refetch } = useWorkspaces()
  const [dialog, setDialog] = useState<Dialog>({ kind: 'none' })
  const close = () => setDialog({ kind: 'none' })

  const toast = useToast()
  const createMut = useCreateWorkspace()
  const updateMut = useUpdateWorkspace()
  const deleteMut = useDeleteWorkspace()

  const handleSubmit = (values: WorkspaceFormValues) => {
    if (dialog.kind === 'create') {
      createMut.mutate(values, {
        onSuccess: () => {
          toast.success(`Workspace “${values.name}” created.`)
          close()
        },
        onError: (e) => toast.error(e.message),
      })
    } else if (dialog.kind === 'edit') {
      const body = changedFields(dialog.ws, values)
      if (Object.keys(body).length === 0) {
        close()
        return
      }
      updateMut.mutate(
        { id: dialog.ws.id, body },
        {
          onSuccess: () => {
            toast.success('Workspace updated.')
            close()
          },
          onError: (e) => toast.error(e.message),
        },
      )
    }
  }

  const handleDelete = () => {
    if (dialog.kind !== 'delete') return
    const { ws } = dialog
    deleteMut.mutate(ws.id, {
      onSuccess: () => {
        toast.success(`Workspace “${ws.name}” deleted.`)
        close()
      },
      onError: (e) => toast.error(e.message),
    })
  }

  return (
    <div className="animate-fade-in space-y-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">Workspaces</h1>
          <p className="mt-1 text-[13px] text-ink-muted">
            Top-level containers for your collections.
          </p>
        </div>
        <Button onClick={() => setDialog({ kind: 'create' })}>
          <Plus className="h-4 w-4" />
          New workspace
        </Button>
      </header>

      <WorkspaceList
        data={data}
        isLoading={isLoading}
        isError={isError}
        message={error?.message}
        onRetry={() => void refetch()}
        onCreate={() => setDialog({ kind: 'create' })}
        onEdit={(ws) => setDialog({ kind: 'edit', ws })}
        onDelete={(ws) => setDialog({ kind: 'delete', ws })}
      />

      <WorkspaceFormModal
        open={dialog.kind === 'create' || dialog.kind === 'edit'}
        workspace={dialog.kind === 'edit' ? dialog.ws : undefined}
        submitting={createMut.isPending || updateMut.isPending}
        onSubmit={handleSubmit}
        onClose={close}
      />

      <ConfirmDialog
        open={dialog.kind === 'delete'}
        title="Delete workspace"
        message={
          dialog.kind === 'delete'
            ? `Delete “${dialog.ws.name}”? Its collections, API keys, and documents are permanently removed. This cannot be undone.`
            : ''
        }
        loading={deleteMut.isPending}
        onConfirm={handleDelete}
        onClose={close}
      />
    </div>
  )
}

/** Render the workspace grid across its loading / error / empty / data states. */
function WorkspaceList({
  data,
  isLoading,
  isError,
  message,
  onRetry,
  onCreate,
  onEdit,
  onDelete,
}: {
  data: Workspace[] | undefined
  isLoading: boolean
  isError: boolean
  message?: string
  onRetry: () => void
  onCreate: () => void
  onEdit: (ws: Workspace) => void
  onDelete: (ws: Workspace) => void
}) {
  if (isLoading) {
    return (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-28 rounded-card" />
        ))}
      </div>
    )
  }
  if (isError) {
    return <QueryError title="Could not load workspaces" message={message} onRetry={onRetry} />
  }
  if (!data || data.length === 0) {
    return (
      <EmptyState
        icon={<FolderKanban className="h-6 w-6" />}
        title="No workspaces yet"
        description="Create your first workspace to start organising collections."
        action={<Button onClick={onCreate}>New workspace</Button>}
      />
    )
  }
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {data.map((ws) => (
        <WorkspaceCard key={ws.id} ws={ws} onEdit={onEdit} onDelete={onDelete} />
      ))}
    </div>
  )
}

/** A single workspace card: click to open, with inline edit / delete actions. */
function WorkspaceCard({
  ws,
  onEdit,
  onDelete,
}: {
  ws: Workspace
  onEdit: (ws: Workspace) => void
  onDelete: (ws: Workspace) => void
}) {
  const navigate = useNavigate()
  const stop = (fn: () => void) => (e: MouseEvent) => {
    e.stopPropagation()
    fn()
  }
  return (
    <Card
      interactive
      onClick={() => navigate(`/workspaces/${ws.id}`)}
      className="group flex h-full cursor-pointer flex-col gap-3 p-4"
    >
      <div className="flex items-start gap-3">
        <span
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-control"
          style={{ backgroundColor: `${ws.color}1A`, color: ws.color }}
        >
          <EntityIcon name={ws.icon} className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <h3 className="truncate text-sm font-medium text-ink">{ws.name}</h3>
            {ws.collection_count !== undefined && (
              <Badge>
                <Layers className="h-3 w-3" />
                {ws.collection_count}
              </Badge>
            )}
          </div>
          <p className="mt-0.5 truncate text-xs text-ink-muted">
            {ws.description || 'No description'}
          </p>
        </div>
      </div>
      <div className="flex items-center justify-between">
        <span className="text-xs text-ink-faint">Created {formatDate(ws.created_at)}</span>
        <div className="flex gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
          <Button
            variant="ghost"
            size="sm"
            aria-label={`Edit ${ws.name}`}
            onClick={stop(() => onEdit(ws))}
            className="h-7 w-7 px-0"
          >
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            aria-label={`Delete ${ws.name}`}
            onClick={stop(() => onDelete(ws))}
            className="h-7 w-7 px-0 hover:text-err"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
    </Card>
  )
}

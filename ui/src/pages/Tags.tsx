import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ChevronRight, GitMerge, Pencil, Plus, Tags as TagsIcon, Trash2 } from 'lucide-react'
import {
  useCreateTag,
  useDeleteTag,
  useMergeTag,
  useTagItems,
  useTags,
  useUpdateTag,
  useWorkspace,
} from '../api/hooks'
import type { Tag, TagUpdate } from '../api/types'
import {
  Badge,
  Button,
  Card,
  ColorPicker,
  ConfirmDialog,
  EmptyState,
  Field,
  Input,
  Modal,
  QueryError,
  SWATCHES,
  Select,
  Skeleton,
  useToast,
} from '../components/ui'
import { TagChip } from '../components/tags/TagChip'

type Dialog =
  | { kind: 'none' }
  | { kind: 'create' }
  | { kind: 'edit'; tag: Tag }
  | { kind: 'merge'; tag: Tag }
  | { kind: 'delete'; tag: Tag }

/** Workspace tags: list with counts, create / rename / recolor / merge / delete, correlation view. */
export default function Tags() {
  const { wsId = '' } = useParams()
  const workspace = useWorkspace(wsId)
  const { data, isLoading, isError, error, refetch } = useTags(wsId)
  const [dialog, setDialog] = useState<Dialog>({ kind: 'none' })
  const close = () => setDialog({ kind: 'none' })

  const toast = useToast()
  const createMut = useCreateTag(wsId)
  const updateMut = useUpdateTag(wsId)
  const deleteMut = useDeleteTag(wsId)
  const mergeMut = useMergeTag(wsId)

  const handleSubmit = (values: { name: string; color: string }) => {
    if (dialog.kind === 'create') {
      createMut.mutate(values, {
        onSuccess: () => {
          toast.success(`Tag “${values.name}” created.`)
          close()
        },
        onError: (e) => toast.error(e.message),
      })
    } else if (dialog.kind === 'edit') {
      const body = changedFields(dialog.tag, values)
      if (Object.keys(body).length === 0) return close()
      updateMut.mutate(
        { tagId: dialog.tag.id, body },
        {
          onSuccess: () => {
            toast.success('Tag updated.')
            close()
          },
          onError: (e) => toast.error(e.message),
        },
      )
    }
  }

  const handleMerge = (targetId: string) => {
    if (dialog.kind !== 'merge') return
    mergeMut.mutate(
      { source_id: dialog.tag.id, target_id: targetId },
      {
        onSuccess: () => {
          toast.success('Tags merged.')
          close()
        },
        onError: (e) => toast.error(e.message),
      },
    )
  }

  const handleDelete = () => {
    if (dialog.kind !== 'delete') return
    const { tag } = dialog
    deleteMut.mutate(tag.id, {
      onSuccess: () => {
        toast.success(`Tag “${tag.name}” deleted.`)
        close()
      },
      onError: (e) => toast.error(e.message),
    })
  }

  return (
    <div className="animate-fade-in space-y-6">
      <nav className="flex items-center gap-1.5 text-xs text-ink-muted">
        <Link to="/workspaces" className="hover:text-ink">
          Workspaces
        </Link>
        <ChevronRight className="h-4 w-4 text-ink-faint" />
        <Link to={`/workspaces/${wsId}`} className="hover:text-ink">
          {workspace.data?.name ?? '…'}
        </Link>
        <ChevronRight className="h-4 w-4 text-ink-faint" />
        <span className="text-ink">Tags</span>
      </nav>

      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">Tags</h1>
          <p className="mt-1 text-[13px] text-ink-muted">
            Correlate and filter collections and documents across this workspace.
          </p>
        </div>
        <Button onClick={() => setDialog({ kind: 'create' })}>
          <Plus className="h-5 w-5" />
          New tag
        </Button>
      </header>

      <TagList
        wsId={wsId}
        data={data}
        isLoading={isLoading}
        isError={isError}
        message={error?.message}
        onRetry={() => void refetch()}
        onCreate={() => setDialog({ kind: 'create' })}
        onEdit={(tag) => setDialog({ kind: 'edit', tag })}
        onMerge={(tag) => setDialog({ kind: 'merge', tag })}
        onDelete={(tag) => setDialog({ kind: 'delete', tag })}
      />

      <TagFormModal
        open={dialog.kind === 'create' || dialog.kind === 'edit'}
        tag={dialog.kind === 'edit' ? dialog.tag : undefined}
        submitting={createMut.isPending || updateMut.isPending}
        onSubmit={handleSubmit}
        onClose={close}
      />

      <MergeModal
        open={dialog.kind === 'merge'}
        source={dialog.kind === 'merge' ? dialog.tag : undefined}
        tags={data ?? []}
        submitting={mergeMut.isPending}
        onMerge={handleMerge}
        onClose={close}
      />

      <ConfirmDialog
        open={dialog.kind === 'delete'}
        title="Delete tag"
        message={
          dialog.kind === 'delete'
            ? `Delete “${dialog.tag.name}”? It is removed from every collection and document it tags. This cannot be undone.`
            : ''
        }
        loading={deleteMut.isPending}
        onConfirm={handleDelete}
        onClose={close}
      />
    </div>
  )
}

/** Reduce a form submission to only the fields that changed. */
function changedFields(tag: Tag, values: { name: string; color: string }): TagUpdate {
  const body: TagUpdate = {}
  if (values.name !== tag.name) body.name = values.name
  if (values.color !== (tag.color ?? '')) body.color = values.color
  return body
}

function TagList({
  wsId,
  data,
  isLoading,
  isError,
  message,
  onRetry,
  onCreate,
  onEdit,
  onMerge,
  onDelete,
}: {
  wsId: string
  data: Tag[] | undefined
  isLoading: boolean
  isError: boolean
  message?: string
  onRetry: () => void
  onCreate: () => void
  onEdit: (tag: Tag) => void
  onMerge: (tag: Tag) => void
  onDelete: (tag: Tag) => void
}) {
  if (isLoading) {
    return (
      <div className="space-y-2">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-14 rounded-card" />
        ))}
      </div>
    )
  }
  if (isError) {
    return <QueryError title="Could not load tags" message={message} onRetry={onRetry} />
  }
  if (!data || data.length === 0) {
    return (
      <EmptyState
        icon={<TagsIcon className="h-7 w-7" />}
        title="No tags yet"
        description="Create a tag to correlate and filter collections and documents."
        action={<Button onClick={onCreate}>New tag</Button>}
      />
    )
  }
  return (
    <div className="space-y-2">
      {data.map((tag) => (
        <TagRow
          key={tag.id}
          wsId={wsId}
          tag={tag}
          onEdit={onEdit}
          onMerge={onMerge}
          onDelete={onDelete}
        />
      ))}
    </div>
  )
}

/** One tag row: chip + usage counts + actions; click to reveal correlated items. */
function TagRow({
  wsId,
  tag,
  onEdit,
  onMerge,
  onDelete,
}: {
  wsId: string
  tag: Tag
  onEdit: (tag: Tag) => void
  onMerge: (tag: Tag) => void
  onDelete: (tag: Tag) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <Card className="group p-3">
      <div className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex min-w-0 flex-1 items-center gap-3 text-left"
          aria-expanded={open}
        >
          <TagChip name={tag.name} color={tag.color} />
          <span className="flex gap-1.5 text-ink-muted">
            <Badge>{tag.collection_count} col</Badge>
            <Badge>{tag.document_count} doc</Badge>
            {tag.workspace_count > 0 && <Badge>workspace</Badge>}
          </span>
        </button>
        <div className="flex gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
          <Button variant="ghost" size="sm" aria-label={`Edit ${tag.name}`}
            onClick={() => onEdit(tag)} className="h-7 w-7 px-0">
            <Pencil className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="sm" aria-label={`Merge ${tag.name}`}
            onClick={() => onMerge(tag)} className="h-7 w-7 px-0">
            <GitMerge className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="sm" aria-label={`Delete ${tag.name}`}
            onClick={() => onDelete(tag)} className="h-7 w-7 px-0 hover:text-err">
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </div>
      {open && <ItemsPanel wsId={wsId} tag={tag} />}
    </Card>
  )
}

/** Correlated collections + documents for one tag, loaded on first expand. */
function ItemsPanel({ wsId, tag }: { wsId: string; tag: Tag }) {
  const { data, isLoading, isError, error, refetch } = useTagItems(wsId, tag.id, true)
  if (isLoading) return <Skeleton className="mt-3 h-16 rounded-control" />
  if (isError) {
    return (
      <div className="mt-3">
        <QueryError title="Could not load items" message={error?.message} onRetry={() => void refetch()} />
      </div>
    )
  }
  const empty = !data || (data.collections.length === 0 && data.documents.length === 0)
  if (empty) {
    return <p className="mt-3 text-xs text-ink-faint">Not assigned to anything yet.</p>
  }
  return (
    <div className="mt-3 grid grid-cols-1 gap-3 border-t border-border pt-3 text-[13px] sm:grid-cols-2">
      <ItemColumn label="Collections" items={data.collections.map((c) => c.name)} />
      <ItemColumn label="Documents" items={data.documents.map((d) => d.filename)} />
    </div>
  )
}

function ItemColumn({ label, items }: { label: string; items: string[] }) {
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-ink-muted">
        {label} ({items.length})
      </p>
      {items.length === 0 ? (
        <p className="text-xs text-ink-faint">None</p>
      ) : (
        <ul className="space-y-0.5">
          {items.map((name, i) => (
            <li key={i} className="truncate text-ink">
              {name}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Create / edit modal: name + color. */
function TagFormModal({
  open,
  tag,
  submitting,
  onSubmit,
  onClose,
}: {
  open: boolean
  tag?: Tag
  submitting: boolean
  onSubmit: (values: { name: string; color: string }) => void
  onClose: () => void
}) {
  const [name, setName] = useState('')
  const [color, setColor] = useState<string>(SWATCHES[6])

  useEffect(() => {
    if (open) {
      setName(tag?.name ?? '')
      setColor(tag?.color || SWATCHES[6])
    }
  }, [open, tag])

  const editing = Boolean(tag)
  const trimmed = name.trim()
  const submit = () => {
    if (trimmed) onSubmit({ name: trimmed, color })
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={editing ? 'Edit tag' : 'New tag'}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={submit} loading={submitting} disabled={!trimmed}>
            {editing ? 'Save changes' : 'Create'}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="Name" htmlFor="tag-name" hint="Lowercased and trimmed on save.">
          <Input
            id="tag-name"
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit()
            }}
            placeholder="e.g. finance"
          />
        </Field>
        <Field label="Color">
          <ColorPicker value={color} onChange={setColor} />
        </Field>
      </div>
    </Modal>
  )
}

/** Merge `source` into a chosen target tag; the source is then deleted. */
function MergeModal({
  open,
  source,
  tags,
  submitting,
  onMerge,
  onClose,
}: {
  open: boolean
  source?: Tag
  tags: Tag[]
  submitting: boolean
  onMerge: (targetId: string) => void
  onClose: () => void
}) {
  const [target, setTarget] = useState('')
  const candidates = tags.filter((t) => t.id !== source?.id)

  useEffect(() => {
    if (open) setTarget('')
  }, [open, source])

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Merge tag"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={() => target && onMerge(target)} loading={submitting} disabled={!target}>
            Merge
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-[13px] text-ink-muted">
          Move every assignment of <span className="font-medium text-ink">{source?.name}</span> onto
          another tag, then delete it.
        </p>
        <Field label="Merge into" htmlFor="merge-target">
          {candidates.length === 0 ? (
            <p className="text-xs text-ink-faint">No other tags to merge into.</p>
          ) : (
            <Select id="merge-target" value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">Select a tag…</option>
              {candidates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
      </div>
    </Modal>
  )
}

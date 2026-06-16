import { useState, type MouseEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ChevronRight, FileText, KeyRound, Layers, Pencil, Plus, Sparkles, Tags as TagsIcon, Trash2 } from 'lucide-react'
import {
  useApplyTagsByName,
  useAssignCollectionTag,
  useCollections,
  useCreateCollection,
  useCreateTag,
  useDeleteCollection,
  useSuggestCollectionTags,
  useUnassignCollectionTag,
  useUpdateCollection,
  useWorkspace,
} from '../api/hooks'
import type { Collection, CollectionUpdate } from '../api/types'
import { SuggestTagsModal } from '../components/tags/SuggestTagsModal'
import { TagChip } from '../components/tags/TagChip'
import { TagFilterBar } from '../components/tags/TagFilterBar'
import { TagPicker } from '../components/tags/TagPicker'
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
  CollectionFormModal,
  type CollectionFormValues,
} from '../components/collections/CollectionFormModal'
import { ApiKeysModal } from '../components/collections/ApiKeysModal'
import { formatDate } from '../lib/format'

/** Which dialog (if any) is currently open, plus the row it acts on. */
type Dialog =
  | { kind: 'none' }
  | { kind: 'create' }
  | { kind: 'edit'; col: Collection }
  | { kind: 'delete'; col: Collection }
  | { kind: 'keys'; col: Collection }

/** Reduce a full form submission to only the fields that actually changed. */
function changedFields(col: Collection, values: CollectionFormValues): CollectionUpdate {
  const body: CollectionUpdate = {}
  if (values.name !== col.name) body.name = values.name
  if (values.description !== (col.description ?? '')) body.description = values.description
  if (values.color !== col.color) body.color = values.color
  if (values.icon !== col.icon) body.icon = values.icon
  return body
}

/** Collections within a workspace: list, create, edit, delete, and key management. */
export default function Collections() {
  const { wsId = '' } = useParams()
  const navigate = useNavigate()
  const workspace = useWorkspace(wsId)
  const { data, isLoading, isError, error, refetch } = useCollections(wsId)
  const [dialog, setDialog] = useState<Dialog>({ kind: 'none' })
  const [tagFilter, setTagFilter] = useState<string[]>([])
  const close = () => setDialog({ kind: 'none' })

  const toggleTag = (name: string) =>
    setTagFilter((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    )
  const shown = data?.filter((col) =>
    tagFilter.every((name) => col.tags?.some((t) => t.name === name)),
  )

  const toast = useToast()
  const createMut = useCreateCollection(wsId)
  const updateMut = useUpdateCollection(wsId)
  const deleteMut = useDeleteCollection(wsId)

  const handleSubmit = (values: CollectionFormValues) => {
    if (dialog.kind === 'create') {
      createMut.mutate(values, {
        onSuccess: () => {
          toast.success(`Collection “${values.name}” created.`)
          close()
        },
        onError: (e) => toast.error(e.message),
      })
    } else if (dialog.kind === 'edit') {
      const body = changedFields(dialog.col, values)
      if (Object.keys(body).length === 0) {
        close()
        return
      }
      updateMut.mutate(
        { colId: dialog.col.id, body },
        {
          onSuccess: () => {
            toast.success('Collection updated.')
            close()
          },
          onError: (e) => toast.error(e.message),
        },
      )
    }
  }

  const handleDelete = () => {
    if (dialog.kind !== 'delete') return
    const { col } = dialog
    deleteMut.mutate(col.id, {
      onSuccess: () => {
        toast.success(`Collection “${col.name}” deleted.`)
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
        <ChevronRight className="h-3.5 w-3.5 text-ink-faint" />
        <span className="text-ink">{workspace.data?.name ?? '…'}</span>
      </nav>

      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">Collections</h1>
          <p className="mt-1 text-[13px] text-ink-muted">
            Searchable document sets within this workspace.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => navigate(`/workspaces/${wsId}/tags`)}>
            <TagsIcon className="h-4 w-4" />
            Tags
          </Button>
          <Button onClick={() => setDialog({ kind: 'create' })}>
            <Plus className="h-4 w-4" />
            New collection
          </Button>
        </div>
      </header>

      <TagFilterBar wsId={wsId} selected={tagFilter} onToggle={toggleTag} />

      <CollectionList
        wsId={wsId}
        data={shown}
        isLoading={isLoading}
        isError={isError}
        message={error?.message}
        onRetry={() => void refetch()}
        onCreate={() => setDialog({ kind: 'create' })}
        onEdit={(col) => setDialog({ kind: 'edit', col })}
        onDelete={(col) => setDialog({ kind: 'delete', col })}
        onKeys={(col) => setDialog({ kind: 'keys', col })}
      />

      <CollectionFormModal
        open={dialog.kind === 'create' || dialog.kind === 'edit'}
        collection={dialog.kind === 'edit' ? dialog.col : undefined}
        submitting={createMut.isPending || updateMut.isPending}
        onSubmit={handleSubmit}
        onClose={close}
      />

      <ConfirmDialog
        open={dialog.kind === 'delete'}
        title="Delete collection"
        message={
          dialog.kind === 'delete'
            ? `Delete “${dialog.col.name}”? Its API keys, documents, and indexed vectors are permanently removed. This cannot be undone.`
            : ''
        }
        loading={deleteMut.isPending}
        onConfirm={handleDelete}
        onClose={close}
      />

      <ApiKeysModal
        open={dialog.kind === 'keys'}
        wsId={wsId}
        colId={dialog.kind === 'keys' ? dialog.col.id : ''}
        collectionName={dialog.kind === 'keys' ? dialog.col.name : ''}
        onClose={close}
      />
    </div>
  )
}

/** Render the collection grid across its loading / error / empty / data states. */
function CollectionList({
  wsId,
  data,
  isLoading,
  isError,
  message,
  onRetry,
  onCreate,
  onEdit,
  onDelete,
  onKeys,
}: {
  wsId: string
  data: Collection[] | undefined
  isLoading: boolean
  isError: boolean
  message?: string
  onRetry: () => void
  onCreate: () => void
  onEdit: (col: Collection) => void
  onDelete: (col: Collection) => void
  onKeys: (col: Collection) => void
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
    return <QueryError title="Could not load collections" message={message} onRetry={onRetry} />
  }
  if (!data || data.length === 0) {
    return (
      <EmptyState
        icon={<Layers className="h-6 w-6" />}
        title="No collections yet"
        description="Create a collection to start ingesting and searching documents."
        action={<Button onClick={onCreate}>New collection</Button>}
      />
    )
  }
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {data.map((col) => (
        <CollectionCard
          key={col.id}
          wsId={wsId}
          col={col}
          onEdit={onEdit}
          onDelete={onDelete}
          onKeys={onKeys}
        />
      ))}
    </div>
  )
}

/** A single collection card: click to open documents, with inline actions. */
function CollectionCard({
  wsId,
  col,
  onEdit,
  onDelete,
  onKeys,
}: {
  wsId: string
  col: Collection
  onEdit: (col: Collection) => void
  onDelete: (col: Collection) => void
  onKeys: (col: Collection) => void
}) {
  const navigate = useNavigate()
  const toast = useToast()
  const assignMut = useAssignCollectionTag(wsId)
  const unassignMut = useUnassignCollectionTag(wsId)
  const createMut = useCreateTag(wsId)
  const tagBusy = assignMut.isPending || unassignMut.isPending || createMut.isPending
  const onErr = (e: Error) => toast.error(e.message)

  const suggestMut = useSuggestCollectionTags(wsId, col.id)
  const applyTags = useApplyTagsByName(wsId)
  const [suggestOpen, setSuggestOpen] = useState(false)
  const [applying, setApplying] = useState(false)

  const openSuggest = () => {
    setSuggestOpen(true)
    suggestMut.mutate()
  }
  const handleApply = (names: string[]) => {
    setApplying(true)
    applyTags(names, (tagId) => assignMut.mutateAsync({ colId: col.id, tagId }))
      .then(() => {
        toast.success(`Applied ${names.length} tag${names.length === 1 ? '' : 's'}.`)
        setSuggestOpen(false)
      })
      .catch((e) => onErr(e as Error))
      .finally(() => setApplying(false))
  }

  const handleCreate = (name: string) =>
    createMut.mutate(
      { name },
      {
        onSuccess: (tag) => assignMut.mutate({ colId: col.id, tagId: tag.id }, { onError: onErr }),
        onError: onErr,
      },
    )

  const stop = (fn: () => void) => (e: MouseEvent) => {
    e.stopPropagation()
    fn()
  }
  return (
    <Card
      interactive
      onClick={() => navigate(`/workspaces/${wsId}/collections/${col.id}`)}
      className="group flex h-full cursor-pointer flex-col gap-3 p-4"
    >
      <div className="flex items-start gap-3">
        <span
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-control"
          style={{ backgroundColor: `${col.color}1A`, color: col.color }}
        >
          <EntityIcon name={col.icon} className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <h3 className="truncate text-sm font-medium text-ink">{col.name}</h3>
            {col.document_count !== undefined && (
              <Badge>
                <FileText className="h-3 w-3" />
                {col.document_count}
              </Badge>
            )}
          </div>
          <p className="mt-0.5 truncate text-xs text-ink-muted">
            {col.description || 'No description'}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
        {(col.tags ?? []).map((t) => (
          <TagChip
            key={t.id}
            name={t.name}
            color={t.color}
            onRemove={() =>
              unassignMut.mutate({ colId: col.id, tagId: t.id }, { onError: onErr })
            }
          />
        ))}
        <TagPicker
          wsId={wsId}
          assigned={col.tags ?? []}
          busy={tagBusy}
          onAssign={(tagId) => assignMut.mutate({ colId: col.id, tagId }, { onError: onErr })}
          onUnassign={(tagId) => unassignMut.mutate({ colId: col.id, tagId }, { onError: onErr })}
          onCreate={handleCreate}
        />
        <button
          type="button"
          onClick={openSuggest}
          className="inline-flex items-center gap-1 rounded-full border border-dashed border-border px-2 py-0.5 text-xs text-ink-muted transition-colors hover:border-accent hover:text-ink"
        >
          <Sparkles className="h-3 w-3" />
          Suggest
        </button>
        {/* Kept inside the stop-propagation wrapper: the modal is portaled, but
            React events bubble through the JSX tree to the card's navigate. */}
        <SuggestTagsModal
          open={suggestOpen}
          onClose={() => setSuggestOpen(false)}
          suggestions={suggestMut.data?.suggestions ?? []}
          loading={suggestMut.isPending}
          error={suggestMut.error?.message}
          applying={applying}
          onApply={handleApply}
        />
      </div>
      <div className="flex items-center justify-between">
        <span className="text-xs text-ink-faint">Created {formatDate(col.created_at)}</span>
        <div className="flex gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
          <Button
            variant="ghost"
            size="sm"
            aria-label={`Manage keys for ${col.name}`}
            onClick={stop(() => onKeys(col))}
            className="h-7 w-7 px-0"
          >
            <KeyRound className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            aria-label={`Edit ${col.name}`}
            onClick={stop(() => onEdit(col))}
            className="h-7 w-7 px-0"
          >
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            aria-label={`Delete ${col.name}`}
            onClick={stop(() => onDelete(col))}
            className="h-7 w-7 px-0 hover:text-err"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
    </Card>
  )
}

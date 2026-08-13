import { useMemo, useState, type MouseEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ChevronRight, FileText, Layers, Pencil, Plus, Tags as TagsIcon, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  useAssignCollectionTag,
  useCollections,
  useCreateCollection,
  useCreateTag,
  useDeleteCollection,
  useUnassignCollectionTag,
  useUpdateCollection,
  useWorkspace,
} from '../api/hooks'
import type { Collection, CollectionUpdate } from '../api/types'
import { TagChip } from '../components/tags/TagChip'
import { collectTags, TagFilterBar } from '../components/tags/TagFilterBar'
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
import { apiErrorMessage } from '../i18n/apiError'
import { formatDate } from '../lib/format'
import { useAuth } from '../auth/AuthContext'

/** Which dialog (if any) is currently open, plus the row it acts on. */
type Dialog =
  | { kind: 'none' }
  | { kind: 'create' }
  | { kind: 'edit'; col: Collection }
  | { kind: 'delete'; col: Collection }

/** Reduce a full form submission to only the fields that actually changed. */
function changedFields(col: Collection, values: CollectionFormValues): CollectionUpdate {
  const body: CollectionUpdate = {}
  if (values.name !== col.name) body.name = values.name
  if (values.description !== (col.description ?? '')) body.description = values.description
  if (values.color !== col.color) body.color = values.color
  if (values.icon !== col.icon) body.icon = values.icon
  return body
}

/** Collections within a workspace: list, create, edit, and delete. */
export default function Collections() {
  const { t } = useTranslation()
  const { wsId = '' } = useParams()
  const navigate = useNavigate()
  const workspace = useWorkspace(wsId)
  // Creating a collection needs write on the workspace; the API reports it per workspace.
  const canWrite = workspace.data?.can_write ?? false
  // Tag management + the Tags page need admin or the `manage_tags` capability.
  const { isAdmin, canManageTags } = useAuth()
  const canTag = isAdmin || canManageTags
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
  // Only offer tags that actually appear on this workspace's collections — not the
  // whole (possibly dozens-strong) workspace tag vocabulary.
  const filterTags = useMemo(() => collectTags(data), [data])

  const toast = useToast()
  const createMut = useCreateCollection(wsId)
  const updateMut = useUpdateCollection(wsId)
  const deleteMut = useDeleteCollection(wsId)

  const handleSubmit = (values: CollectionFormValues) => {
    if (dialog.kind === 'create') {
      createMut.mutate(values, {
        onSuccess: () => {
          toast.success(t('collections.toast.created', { name: values.name }))
          close()
        },
        onError: (e) => toast.error(apiErrorMessage(e, t)),
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
            toast.success(t('collections.toast.updated'))
            close()
          },
          onError: (e) => toast.error(apiErrorMessage(e, t)),
        },
      )
    }
  }

  const handleDelete = () => {
    if (dialog.kind !== 'delete') return
    const { col } = dialog
    deleteMut.mutate(col.id, {
      onSuccess: () => {
        toast.success(t('collections.toast.deleted', { name: col.name }))
        close()
      },
      onError: (e) => toast.error(apiErrorMessage(e, t)),
    })
  }

  return (
    <div className="animate-fade-in space-y-6">
      <nav className="flex items-center gap-1.5 text-xs text-ink-muted">
        <Link to="/workspaces" className="hover:text-ink">
          {t('nav.workspaces')}
        </Link>
        <ChevronRight className="h-4 w-4 text-ink-faint" />
        <span className="text-ink">{workspace.data?.name ?? '…'}</span>
      </nav>

      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">{t('collections.title')}</h1>
          <p className="mt-1 text-[13px] text-ink-muted">{t('collections.subtitle')}</p>
        </div>
        <div className="flex gap-2">
          {canTag && (
            <Button variant="secondary" onClick={() => navigate(`/workspaces/${wsId}/tags`)}>
              <TagsIcon className="h-5 w-5" />
              {t('common.tags')}
            </Button>
          )}
          {canWrite && (
            <Button onClick={() => setDialog({ kind: 'create' })}>
              <Plus className="h-5 w-5" />
              {t('collections.new')}
            </Button>
          )}
        </div>
      </header>

      <TagFilterBar tags={filterTags} selected={tagFilter} onToggle={toggleTag} />

      <CollectionList
        wsId={wsId}
        data={shown}
        isLoading={isLoading}
        isError={isError}
        message={error ? apiErrorMessage(error, t) : undefined}
        canCreate={canWrite}
        onRetry={() => void refetch()}
        onCreate={() => setDialog({ kind: 'create' })}
        onEdit={(col) => setDialog({ kind: 'edit', col })}
        onDelete={(col) => setDialog({ kind: 'delete', col })}
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
        title={t('collections.delete.title')}
        message={
          dialog.kind === 'delete'
            ? t('collections.delete.message', { name: dialog.col.name })
            : ''
        }
        loading={deleteMut.isPending}
        onConfirm={handleDelete}
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
  canCreate,
  onRetry,
  onCreate,
  onEdit,
  onDelete,
}: {
  wsId: string
  data: Collection[] | undefined
  isLoading: boolean
  isError: boolean
  message?: string
  canCreate: boolean
  onRetry: () => void
  onCreate: () => void
  onEdit: (col: Collection) => void
  onDelete: (col: Collection) => void
}) {
  const { t } = useTranslation()
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
    return <QueryError title={t('collections.error')} message={message} onRetry={onRetry} />
  }
  if (!data || data.length === 0) {
    return (
      <EmptyState
        icon={<Layers className="h-7 w-7" />}
        title={t('collections.empty.title')}
        description={canCreate ? t('collections.empty.canCreate') : t('collections.empty.readonly')}
        action={canCreate ? <Button onClick={onCreate}>{t('collections.new')}</Button> : undefined}
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
}: {
  wsId: string
  col: Collection
  onEdit: (col: Collection) => void
  onDelete: (col: Collection) => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { isAdmin, canManageTags } = useAuth()
  // (Un)assigning a collection tag needs the manage_tags privilege AND write on the collection —
  // mirror the backend (authorize_tag_management + authorize_collection write) so we don't offer a
  // control that would 403. Existing tags still render read-only below without write.
  const canManageColTags = (isAdmin || canManageTags) && (col.can_write ?? false)
  const toast = useToast()
  const assignMut = useAssignCollectionTag(wsId)
  const unassignMut = useUnassignCollectionTag(wsId)
  const createMut = useCreateTag(wsId)
  const tagBusy = assignMut.isPending || unassignMut.isPending || createMut.isPending
  const onErr = (e: Error) => toast.error(apiErrorMessage(e, t))

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
          className="flex h-12 w-12 shrink-0 items-center justify-center rounded-control"
          style={{ backgroundColor: `${col.color}1A`, color: col.color }}
        >
          <EntityIcon name={col.icon} className="h-8 w-8" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <h3 className="truncate text-sm font-medium text-ink">{col.name}</h3>
            {col.document_count !== undefined && (
              <Badge>
                <FileText className="h-3.5 w-3.5" />
                {col.document_count}
              </Badge>
            )}
          </div>
          <p className="mt-0.5 truncate text-xs text-ink-muted">
            {col.description || t('common.noDescription')}
          </p>
        </div>
      </div>
      {((col.tags ?? []).length > 0 || canManageColTags) && (
        <div className="flex flex-wrap items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
          {(col.tags ?? []).map((tag) => (
            <TagChip
              key={tag.id}
              name={tag.name}
              color={tag.color}
              onRemove={
                canManageColTags
                  ? () => unassignMut.mutate({ colId: col.id, tagId: tag.id }, { onError: onErr })
                  : undefined
              }
            />
          ))}
          {canManageColTags && (
            <TagPicker
              wsId={wsId}
              assigned={col.tags ?? []}
              busy={tagBusy}
              onAssign={(tagId) => assignMut.mutate({ colId: col.id, tagId }, { onError: onErr })}
              onUnassign={(tagId) =>
                unassignMut.mutate({ colId: col.id, tagId }, { onError: onErr })
              }
              onCreate={handleCreate}
            />
          )}
        </div>
      )}
      <div className="flex items-center justify-between">
        <span className="text-xs text-ink-faint">
          {t('common.created', { date: formatDate(col.created_at) })}
        </span>
        {col.can_write && (
          <div className="flex gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
            <Button
              variant="ghost"
              size="sm"
              aria-label={t('common.editAria', { name: col.name })}
              onClick={stop(() => onEdit(col))}
              className="h-10 w-10 px-0"
            >
              <Pencil className="h-7 w-7" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              aria-label={t('common.deleteAria', { name: col.name })}
              onClick={stop(() => onDelete(col))}
              className="h-10 w-10 px-0 hover:text-err"
            >
              <Trash2 className="h-7 w-7" />
            </Button>
          </div>
        )}
      </div>
    </Card>
  )
}

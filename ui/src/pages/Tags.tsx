import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ChevronRight, GitMerge, Pencil, Plus, Tags as TagsIcon, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
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
import { apiErrorMessage } from '../i18n/apiError'
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
  const { t } = useTranslation()
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
          toast.success(t('tags.toast.created', { name: values.name }))
          close()
        },
        onError: (e) => toast.error(apiErrorMessage(e, t)),
      })
    } else if (dialog.kind === 'edit') {
      const body = changedFields(dialog.tag, values)
      if (Object.keys(body).length === 0) return close()
      updateMut.mutate(
        { tagId: dialog.tag.id, body },
        {
          onSuccess: () => {
            toast.success(t('tags.toast.updated'))
            close()
          },
          onError: (e) => toast.error(apiErrorMessage(e, t)),
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
          toast.success(t('tags.toast.merged'))
          close()
        },
        onError: (e) => toast.error(apiErrorMessage(e, t)),
      },
    )
  }

  const handleDelete = () => {
    if (dialog.kind !== 'delete') return
    const { tag } = dialog
    deleteMut.mutate(tag.id, {
      onSuccess: () => {
        toast.success(t('tags.toast.deleted', { name: tag.name }))
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
        <Link to={`/workspaces/${wsId}`} className="hover:text-ink">
          {workspace.data?.name ?? '…'}
        </Link>
        <ChevronRight className="h-4 w-4 text-ink-faint" />
        <span className="text-ink">{t('common.tags')}</span>
      </nav>

      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">{t('common.tags')}</h1>
          <p className="mt-1 text-[13px] text-ink-muted">
            {t('tags.subtitle')}
          </p>
        </div>
        <Button onClick={() => setDialog({ kind: 'create' })}>
          <Plus className="h-5 w-5" />
          {t('tags.new')}
        </Button>
      </header>

      <TagList
        wsId={wsId}
        data={data}
        isLoading={isLoading}
        isError={isError}
        message={error ? apiErrorMessage(error, t) : undefined}
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
        title={t('tags.delete.title')}
        message={
          dialog.kind === 'delete'
            ? t('tags.delete.message', { name: dialog.tag.name })
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
  const { t } = useTranslation()
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
    return <QueryError title={t('tags.error')} message={message} onRetry={onRetry} />
  }
  if (!data || data.length === 0) {
    return (
      <EmptyState
        icon={<TagsIcon className="h-7 w-7" />}
        title={t('tags.empty.title')}
        description={t('tags.empty.description')}
        action={<Button onClick={onCreate}>{t('tags.new')}</Button>}
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
  const { t } = useTranslation()
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
            <Badge>{t('tags.count.col', { count: tag.collection_count })}</Badge>
            <Badge>{t('tags.count.doc', { count: tag.document_count })}</Badge>
            {tag.workspace_count > 0 && <Badge>{t('tags.workspaceBadge')}</Badge>}
          </span>
        </button>
        <div className="flex gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
          <Button variant="ghost" size="sm" aria-label={t('common.editAria', { name: tag.name })}
            onClick={() => onEdit(tag)} className="h-7 w-7 px-0">
            <Pencil className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="sm" aria-label={t('tags.mergeAria', { name: tag.name })}
            onClick={() => onMerge(tag)} className="h-7 w-7 px-0">
            <GitMerge className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="sm" aria-label={t('common.deleteAria', { name: tag.name })}
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
  const { t } = useTranslation()
  const { data, isLoading, isError, error, refetch } = useTagItems(wsId, tag.id, true)
  if (isLoading) return <Skeleton className="mt-3 h-16 rounded-control" />
  if (isError) {
    return (
      <div className="mt-3">
        <QueryError
          title={t('tags.itemsError')}
          message={error ? apiErrorMessage(error, t) : undefined}
          onRetry={() => void refetch()}
        />
      </div>
    )
  }
  const empty = !data || (data.collections.length === 0 && data.documents.length === 0)
  if (empty) {
    return <p className="mt-3 text-xs text-ink-faint">{t('tags.notAssigned')}</p>
  }
  return (
    <div className="mt-3 grid grid-cols-1 gap-3 border-t border-border pt-3 text-[13px] sm:grid-cols-2">
      <ItemColumn label={t('collections.title')} items={data.collections.map((c) => c.name)} />
      <ItemColumn label={t('documents.title')} items={data.documents.map((d) => d.filename)} />
    </div>
  )
}

function ItemColumn({ label, items }: { label: string; items: string[] }) {
  const { t } = useTranslation()
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-ink-muted">
        {label} ({items.length})
      </p>
      {items.length === 0 ? (
        <p className="text-xs text-ink-faint">{t('tags.none')}</p>
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
  const { t } = useTranslation()
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
      title={editing ? t('tags.editTitle') : t('tags.new')}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={submitting}>
            {t('common.cancel')}
          </Button>
          <Button onClick={submit} loading={submitting} disabled={!trimmed}>
            {editing ? t('common.saveChanges') : t('common.create')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label={t('common.name')} htmlFor="tag-name" hint={t('tags.nameHint')}>
          <Input
            id="tag-name"
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit()
            }}
            placeholder={t('tags.namePlaceholder')}
          />
        </Field>
        <Field label={t('common.color')}>
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
  const { t } = useTranslation()
  const [target, setTarget] = useState('')
  const candidates = tags.filter((tag) => tag.id !== source?.id)

  useEffect(() => {
    if (open) setTarget('')
  }, [open, source])

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('tags.mergeTitle')}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={submitting}>
            {t('common.cancel')}
          </Button>
          <Button onClick={() => target && onMerge(target)} loading={submitting} disabled={!target}>
            {t('tags.merge')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-[13px] text-ink-muted">
          {t('tags.mergeIntro.before')}
          <span className="font-medium text-ink">{source?.name}</span>
          {t('tags.mergeIntro.after')}
        </p>
        <Field label={t('tags.mergeInto')} htmlFor="merge-target">
          {candidates.length === 0 ? (
            <p className="text-xs text-ink-faint">{t('tags.noMergeTargets')}</p>
          ) : (
            <Select id="merge-target" value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">{t('tags.selectTag')}</option>
              {candidates.map((tag) => (
                <option key={tag.id} value={tag.id}>
                  {tag.name}
                </option>
              ))}
            </Select>
          )}
        </Field>
      </div>
    </Modal>
  )
}

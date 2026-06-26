import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { AlertCircle, ChevronRight, Database, DatabaseZap, Download, ExternalLink, FileText, Sparkles, Trash2 } from 'lucide-react'
import {
  useApplyTagsByName,
  useAssignDocumentTag,
  useCollection,
  useCreateTag,
  useDeleteDocument,
  useDocumentStatus,
  useDocuments,
  useIndexDocument,
  useSuggestDocumentTags,
  useUnassignDocumentTag,
  useUploadDocument,
  useWorkspace,
} from '../api/hooks'
import { api } from '../api/client'
import type { DocumentSummary } from '../api/types'
import { SuggestTagsModal } from '../components/tags/SuggestTagsModal'
import { TagChip } from '../components/tags/TagChip'
import { collectTags, TagFilterBar } from '../components/tags/TagFilterBar'
import { TagPicker } from '../components/tags/TagPicker'
import {
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  QueryError,
  Skeleton,
  StatusBadge,
  useToast,
} from '../components/ui'
import { UploadZone } from '../components/documents/UploadZone'
import { formatBytes, timeAgo } from '../lib/format'

/** Largest file accepted before an upload is attempted (client-side guard). */
const MAX_FILE_SIZE_MB = 50

/** Documents within a collection: upload, live ingestion status, and delete. */
export default function Documents() {
  const { wsId = '', colId = '' } = useParams()
  const workspace = useWorkspace(wsId)
  const collection = useCollection(wsId, colId)
  const { data, isLoading, isError, error, refetch } = useDocuments(wsId, colId)

  const toast = useToast()
  const uploadMut = useUploadDocument(wsId, colId)
  const deleteMut = useDeleteDocument(wsId, colId)
  const [uploading, setUploading] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<DocumentSummary | null>(null)
  const [tagFilter, setTagFilter] = useState<string[]>([])

  const toggleTag = (name: string) =>
    setTagFilter((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    )
  const shown = data?.filter((doc) =>
    tagFilter.every((name) => doc.tags?.some((t) => t.name === name)),
  )
  // Only offer tags present on this collection's documents, not the whole workspace.
  const filterTags = useMemo(() => collectTags(data), [data])

  const handleFiles = async (files: File[]) => {
    const maxBytes = MAX_FILE_SIZE_MB * 1024 * 1024
    const valid: File[] = []
    for (const f of files) {
      if (f.size > maxBytes) {
        toast.error(`${f.name} is larger than ${MAX_FILE_SIZE_MB} MB and was skipped.`)
      } else {
        valid.push(f)
      }
    }
    if (valid.length === 0) return
    setUploading(true)
    let ok = 0
    for (const f of valid) {
      try {
        await uploadMut.mutateAsync(f)
        ok += 1
      } catch (e) {
        toast.error(`${f.name}: ${(e as Error).message}`)
      }
    }
    setUploading(false)
    if (ok > 0) toast.success(`${ok} file${ok === 1 ? '' : 's'} queued for ingestion.`)
  }

  const handleDelete = () => {
    if (!deleteTarget) return
    const doc = deleteTarget
    deleteMut.mutate(doc.document_id, {
      onSuccess: () => {
        toast.success(`Deleted “${doc.filename}”.`)
        setDeleteTarget(null)
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
        <span className="text-ink">{collection.data?.name ?? '…'}</span>
      </nav>

      <header>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Documents</h1>
        <p className="mt-1 text-[13px] text-ink-muted">
          Upload files to ingest them into this collection.
        </p>
      </header>

      <UploadZone onFiles={handleFiles} busy={uploading} maxSizeMb={MAX_FILE_SIZE_MB} />

      <TagFilterBar tags={filterTags} selected={tagFilter} onToggle={toggleTag} />

      <DocumentList
        wsId={wsId}
        colId={colId}
        data={shown}
        isLoading={isLoading}
        isError={isError}
        message={error?.message}
        onRetry={() => void refetch()}
        onDelete={setDeleteTarget}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete document"
        message={
          deleteTarget
            ? `Delete “${deleteTarget.filename}”? Its chunks and indexed vectors are removed. This cannot be undone.`
            : ''
        }
        loading={deleteMut.isPending}
        onConfirm={handleDelete}
        onClose={() => setDeleteTarget(null)}
      />
    </div>
  )
}

/** Render the document list across its loading / error / empty / data states. */
function DocumentList({
  wsId,
  colId,
  data,
  isLoading,
  isError,
  message,
  onRetry,
  onDelete,
}: {
  wsId: string
  colId: string
  data: DocumentSummary[] | undefined
  isLoading: boolean
  isError: boolean
  message?: string
  onRetry: () => void
  onDelete: (doc: DocumentSummary) => void
}) {
  if (isLoading) {
    return (
      <Card className="divide-y divide-border">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex items-center justify-between p-4">
            <Skeleton className="h-4 w-56" />
            <Skeleton className="h-5 w-20 rounded-full" />
          </div>
        ))}
      </Card>
    )
  }
  if (isError) {
    return <QueryError title="Could not load documents" message={message} onRetry={onRetry} />
  }
  if (!data || data.length === 0) {
    return (
      <EmptyState
        icon={<FileText className="h-7 w-7" />}
        title="No documents yet"
        description="Drop files into the area above to start ingesting them."
      />
    )
  }
  return (
    <Card className="divide-y divide-border">
      {data.map((doc) => (
        <DocumentRow key={doc.document_id} wsId={wsId} colId={colId} doc={doc} onDelete={onDelete} />
      ))}
    </Card>
  )
}

/** A single document row: metadata, live status, an optional failure reason, delete. */
function DocumentRow({
  wsId,
  colId,
  doc,
  onDelete,
}: {
  wsId: string
  colId: string
  doc: DocumentSummary
  onDelete: (doc: DocumentSummary) => void
}) {
  const [showError, setShowError] = useState(false)
  const failed = doc.status === 'failed'

  const toast = useToast()
  const indexMut = useIndexDocument(wsId, colId)
  const assignMut = useAssignDocumentTag(wsId, colId)
  const unassignMut = useUnassignDocumentTag(wsId, colId)
  const createMut = useCreateTag(wsId)
  const tagBusy = assignMut.isPending || unassignMut.isPending || createMut.isPending
  const onErr = (e: Error) => toast.error(e.message)

  const suggestMut = useSuggestDocumentTags(wsId, colId, doc.document_id)
  const applyTags = useApplyTagsByName(wsId)
  const [suggestOpen, setSuggestOpen] = useState(false)
  const [applying, setApplying] = useState(false)

  // Suggesting reads the document's indexed text, which only exists once ingestion
  // finishes. While it's still being inserted there's nothing to tag, so block it.
  const ingested = doc.status === 'done'
  const openSuggest = () => {
    setSuggestOpen(true)
    suggestMut.mutate()
  }
  const handleApply = (names: string[]) => {
    setApplying(true)
    applyTags(names, (tagId) => assignMut.mutateAsync({ docId: doc.document_id, tagId }))
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
        onSuccess: (tag) =>
          assignMut.mutate({ docId: doc.document_id, tagId: tag.id }, { onError: onErr }),
        onError: onErr,
      },
    )

  return (
    <div className="p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <FileText className="h-7 w-7 shrink-0 text-ink-faint" />
          <div className="min-w-0">
            <p className="truncate text-[13px] font-medium text-ink">{doc.filename}</p>
            <p className="text-xs text-ink-faint">
              {doc.file_type.toUpperCase()} · {formatBytes(doc.file_size)} · updated{' '}
              {timeAgo(doc.updated_at)}
            </p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {failed && (
            <button
              type="button"
              onClick={() => setShowError((v) => !v)}
              className="text-xs font-medium text-err hover:underline"
            >
              {showError ? 'Hide' : 'Why?'}
            </button>
          )}
          <StatusBadge status={doc.status ?? 'pending'} />
          <IndexBadge
            doc={doc}
            busy={indexMut.isPending}
            onIndex={() =>
              indexMut.mutate(doc.document_id, {
                onSuccess: () => toast.success(`Indexing “${doc.filename}”.`),
                onError: onErr,
              })
            }
          />
          <Button
            variant="ghost"
            size="sm"
            aria-label={`Open ${doc.filename}`}
            onClick={() => void api.openDocument(doc.document_id).catch((e) => onErr(e as Error))}
            className="h-10 w-10 px-0"
          >
            <ExternalLink className="h-7 w-7" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            aria-label={`Download ${doc.filename}`}
            onClick={() =>
              void api.downloadDocument(doc.document_id, doc.filename).catch((e) => onErr(e as Error))
            }
            className="h-10 w-10 px-0"
          >
            <Download className="h-7 w-7" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            aria-label={`Delete ${doc.filename}`}
            onClick={() => onDelete(doc)}
            className="h-10 w-10 px-0 hover:text-err"
          >
            <Trash2 className="h-7 w-7" />
          </Button>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {(doc.tags ?? []).map((t) => (
          <TagChip
            key={t.id}
            name={t.name}
            color={t.color}
            onRemove={() =>
              unassignMut.mutate({ docId: doc.document_id, tagId: t.id }, { onError: onErr })
            }
          />
        ))}
        <TagPicker
          wsId={wsId}
          assigned={doc.tags ?? []}
          busy={tagBusy}
          onAssign={(tagId) =>
            assignMut.mutate({ docId: doc.document_id, tagId }, { onError: onErr })
          }
          onUnassign={(tagId) =>
            unassignMut.mutate({ docId: doc.document_id, tagId }, { onError: onErr })
          }
          onCreate={handleCreate}
        />
        <button
          type="button"
          onClick={openSuggest}
          disabled={!ingested}
          title={ingested ? 'Suggest tags from this document' : 'Available once ingestion finishes'}
          className={`inline-flex items-center gap-1 rounded-full border border-dashed px-2 py-0.5 text-xs transition-colors ${
            ingested
              ? 'border-border text-ink-muted hover:border-accent hover:text-ink'
              : 'cursor-not-allowed border-border/60 text-ink-faint opacity-60'
          }`}
        >
          <Sparkles className="h-3.5 w-3.5" />
          Suggest
        </button>
      </div>
      <SuggestTagsModal
        open={suggestOpen}
        onClose={() => setSuggestOpen(false)}
        suggestions={suggestMut.data?.suggestions ?? []}
        loading={suggestMut.isPending}
        error={suggestMut.error?.message}
        applying={applying}
        onApply={handleApply}
      />
      {failed && showError && (
        <FailureReason wsId={wsId} colId={colId} docId={doc.document_id} />
      )}
    </div>
  )
}

/** BM25 index state for a document: a pill when indexed, a trigger when not.
 *
 * Only shown once ingestion is done — there are no chunks to index before that.
 */
function IndexBadge({
  doc,
  busy,
  onIndex,
}: {
  doc: DocumentSummary
  busy: boolean
  onIndex: () => void
}) {
  if (doc.status !== 'done') return null
  if (doc.indexed) {
    return (
      <span
        title="Indexed for BM25 / keyword search"
        className="inline-flex items-center gap-1 rounded-full border border-ok/30 bg-ok/5 px-2 py-0.5 text-xs font-medium text-ok"
      >
        <Database className="h-3.5 w-3.5" />
        Indexed
      </span>
    )
  }
  return (
    <button
      type="button"
      onClick={onIndex}
      disabled={busy}
      title="Not in the keyword index — click to index"
      className="inline-flex items-center gap-1 rounded-full border border-dashed border-warn/40 px-2 py-0.5 text-xs font-medium text-warn transition-colors hover:bg-warn/5 disabled:opacity-60"
    >
      <DatabaseZap className="h-3.5 w-3.5" />
      {busy ? 'Indexing…' : 'Index'}
    </button>
  )
}

/** Lazily fetch and show a failed document's ingestion error. */
function FailureReason({ wsId, colId, docId }: { wsId: string; colId: string; docId: string }) {
  const { data, isLoading, isError } = useDocumentStatus(wsId, colId, docId, true)
  const text = isLoading
    ? 'Loading error…'
    : isError
      ? 'Could not load the error detail.'
      : (data?.error ?? 'No error detail recorded.')
  return (
    <div className="mt-2 flex items-start gap-2 rounded-control border border-err/30 bg-err/5 px-3 py-2">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-err" />
      <p className="break-words text-xs text-ink-muted">{text}</p>
    </div>
  )
}

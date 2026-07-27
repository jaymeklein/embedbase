import { useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { AlertCircle, ChevronRight, Database, DatabaseZap, Download, ExternalLink, FileText, Trash2 } from 'lucide-react'
import {
  useAssignDocumentTag,
  useCollection,
  useCreateTag,
  useDeleteDocument,
  useDocumentStatus,
  useDocuments,
  useIndexDocument,
  useReprocessDocument,
  useUnassignDocumentTag,
  useUploadDocument,
  useWorkspace,
} from '../api/hooks'
import { api } from '../api/client'
import type { DocumentQuery, DocumentSummary } from '../api/types'
import {
  useIngestionProgress,
  type IngestionProgress,
} from '../realtime/useIngestionProgress'
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
import { DocumentFilters, type DocumentFilterValues } from '../components/documents/DocumentFilters'
import { Pager } from '../components/Pager'
import { formatBytes, timeAgo } from '../lib/format'
import { useAuth } from '../auth/AuthContext'

/** Largest file accepted before an upload is attempted (client-side guard). */
const MAX_FILE_SIZE_MB = 50
/** Per-file temporary-retention bounds (mirror the server's api/constants.py). */
const MIN_RETENTION_DAYS = 1
const MAX_RETENTION_DAYS = 30
const DEFAULT_RETENTION_DAYS = 7
const clampRetention = (n: number) =>
  Math.max(MIN_RETENTION_DAYS, Math.min(MAX_RETENTION_DAYS, Math.round(n) || MIN_RETENTION_DAYS))
/** Documents fetched per page (matches the server default; ≤ its 200 cap). */
const PAGE_SIZE = 50

/** Documents within a collection: upload, live ingestion status, and delete. */
export default function Documents() {
  const { wsId = '', colId = '' } = useParams()
  const workspace = useWorkspace(wsId)
  const collection = useCollection(wsId, colId)
  // Write affordances (upload, delete, retry, index) gate on the collection's can_write (write on
  // it or an ancestor); tag editing is admin-only (master-gated router). Per-document buttons
  // deliberately reuse this collection signal — the backend still authorizes per document, so a
  // rare document-level read grant on a child doc fails closed (button shown, the action 403s).
  const canWrite = collection.data?.can_write ?? false

  const [page, setPage] = useState(0)
  const [searchParams] = useSearchParams()
  // Deep-link from the ingestion queue's "open file": ?filename=… pre-fills the filter so the file
  // is shown at once instead of buried on some page of the full listing.
  const [filters, setFiltersState] = useState<DocumentFilterValues>(() => {
    const filename = searchParams.get('filename')
    return filename ? { filename } : {}
  })
  const [tagFilter, setTagFilter] = useState<string[]>([])

  const query: DocumentQuery = {
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
    ...filters,
    tag: tagFilter.length > 0 ? tagFilter : undefined,
  }
  const { data, isLoading, isError, error, refetch } = useDocuments(wsId, colId, query)
  const items = data?.items ?? []
  const total = data?.total ?? 0
  const hasFilters =
    tagFilter.length > 0 || Object.values(filters).some((v) => v != null && v !== '')
  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1)
  // A total that shrinks (deleting the tail of a page) can strand `page` past the end — clamp it
  // so the pager count and empty state stay coherent instead of showing "51–50 of 50".
  useEffect(() => {
    if (page > lastPage) setPage(lastPage)
  }, [page, lastPage])

  const toast = useToast()
  const uploadMut = useUploadDocument(wsId, colId)
  const deleteMut = useDeleteDocument(wsId, colId)
  const [uploading, setUploading] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<DocumentSummary | null>(null)
  // Per-file retention: null = permanent, or 1-30 days after which the worker sweep
  // auto-deletes the document (checkbox toggles between permanent and a default week).
  const [retentionDays, setRetentionDays] = useState<number | null>(null)

  // Any filter/tag change resets to the first page — the old offset may exceed the new count.
  const setFilters = (next: DocumentFilterValues) => {
    setFiltersState(next)
    setPage(0)
  }
  const toggleTag = (name: string) => {
    setTagFilter((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    )
    setPage(0)
  }
  // Tags offered in the filter bar: those on the current page PLUS any selected tag not on the
  // page, so a selected tag stays deselectable even when it filters the list down to zero rows.
  const tagOptions = useMemo(() => {
    const present = collectTags(data?.items)
    const names = new Set(present.map((t) => t.name))
    const missing = tagFilter
      .filter((n) => !names.has(n))
      .map((n) => ({ id: n, name: n, color: null }))
    return [...present, ...missing]
  }, [data, tagFilter])

  /** Stream the validated files to the server, reporting per-file failures. */
  const uploadFiles = async (valid: File[]) => {
    setUploading(true)
    let ok = 0
    for (const f of valid) {
      try {
        await uploadMut.mutateAsync({ file: f, retentionDays })
        ok += 1
      } catch (e) {
        toast.error(`${f.name}: ${(e as Error).message}`)
      }
    }
    setUploading(false)
    if (ok > 0) toast.success(`${ok} file${ok === 1 ? '' : 's'} queued for ingestion.`)
  }

  const handleFiles = (files: File[]) => {
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
    void uploadFiles(valid)
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
          {canWrite
            ? 'Upload files to ingest them into this collection.'
            : 'Browse and search documents in this collection.'}
        </p>
      </header>

      {canWrite && (
        <UploadZone onFiles={handleFiles} busy={uploading} maxSizeMb={MAX_FILE_SIZE_MB} />
      )}

      {canWrite && (
        <div className="flex flex-wrap items-center gap-2 text-[13px] text-ink-muted">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={retentionDays != null}
              onChange={(e) => setRetentionDays(e.target.checked ? DEFAULT_RETENTION_DAYS : null)}
              className="h-4 w-4 accent-accent"
            />
            Temporary — automatically delete after
          </label>
          <input
            type="number"
            min={MIN_RETENTION_DAYS}
            max={MAX_RETENTION_DAYS}
            value={retentionDays ?? DEFAULT_RETENTION_DAYS}
            disabled={retentionDays == null}
            onChange={(e) => setRetentionDays(clampRetention(Number(e.target.value)))}
            aria-label="Retention in days"
            className="w-16 rounded-control border border-border bg-surface px-2 py-1 text-ink disabled:opacity-40"
          />
          <span>day{retentionDays === 1 ? '' : 's'}</span>
        </div>
      )}

      <DocumentFilters value={filters} onChange={setFilters} />

      {tagOptions.length > 0 && (
        <TagFilterBar tags={tagOptions} selected={tagFilter} onToggle={toggleTag} />
      )}

      <DocumentList
        wsId={wsId}
        colId={colId}
        canWrite={canWrite}
        data={items}
        filtered={hasFilters}
        isLoading={isLoading}
        isError={isError}
        message={error?.message}
        onRetry={() => void refetch()}
        onDelete={setDeleteTarget}
      />

      <Pager
        page={page}
        pageSize={PAGE_SIZE}
        total={total}
        onPage={setPage}
        loading={isLoading}
        sticky
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
  canWrite,
  data,
  filtered,
  isLoading,
  isError,
  message,
  onRetry,
  onDelete,
}: {
  wsId: string
  colId: string
  canWrite: boolean
  data: DocumentSummary[] | undefined
  filtered: boolean
  isLoading: boolean
  isError: boolean
  message?: string
  onRetry: () => void
  onDelete: (doc: DocumentSummary) => void
}) {
  const progressById = useIngestionProgress(wsId, colId)
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
    return filtered ? (
      <EmptyState
        icon={<FileText className="h-7 w-7" />}
        title="No matching documents"
        description="No documents match the current filters. Adjust or clear them to see more."
      />
    ) : (
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
        <DocumentRow
          key={doc.document_id}
          wsId={wsId}
          colId={colId}
          canWrite={canWrite}
          doc={doc}
          progress={progressById[doc.document_id]}
          onDelete={onDelete}
        />
      ))}
    </Card>
  )
}

/** Inline progress bar for active rows — determinate when a page/batch total is known,
 *  an indeterminate shimmer otherwise (Docling, the queued wait, the upload). The label
 *  is driven by whether ingestion has actually started: "Uploading" until the first WS
 *  progress event lands (bytes POSTing, then queued), "Ingesting" once the worker is
 *  parsing/embedding/storing. doc.status can't drive this — the list only refetches on
 *  the terminal event, so it's stale at "pending" for the whole run. */
function IngestProgress({ progress }: { progress?: IngestionProgress }) {
  const pct = progress?.pct ?? null
  const determinate = pct != null
  const label = progress ? 'Ingesting' : 'Uploading'
  return (
    <div className="flex w-40 shrink-0 flex-col gap-1">
      <div className="flex items-center justify-between text-xs text-ink-muted">
        <span>{label}</span>
        {determinate && <span className="tabular-nums">{pct}%</span>}
      </div>
      <div className="relative h-1.5 overflow-hidden rounded-full bg-canvas">
        {determinate ? (
          <div
            className="h-full rounded-full bg-accent transition-[width] duration-300"
            style={{ width: `${pct}%` }}
          />
        ) : (
          // ponytail: reuse the Skeleton shimmer (a swept band, not a full bar) so the
          // indeterminate state never flashes as 100% before the first real pct lands.
          <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-accent/70 to-transparent" />
        )}
      </div>
    </div>
  )
}

/** A single document row: metadata, live status, an optional failure reason, delete. */
function DocumentRow({
  wsId,
  colId,
  canWrite,
  doc,
  progress,
  onDelete,
}: {
  wsId: string
  colId: string
  canWrite: boolean
  doc: DocumentSummary
  progress?: IngestionProgress
  onDelete: (doc: DocumentSummary) => void
}) {
  const { isAdmin, canManageTags } = useAuth()
  // (Un)assigning a document tag needs the manage_tags privilege AND write on the resource —
  // mirror the backend (authorize_tag_management + authorize_document write) so we don't show a
  // control that would 403. Existing tags still render read-only below without write.
  const canManageDocTags = (isAdmin || canManageTags) && canWrite
  const [showError, setShowError] = useState(false)
  const failed = doc.status === 'failed'

  const toast = useToast()
  const indexMut = useIndexDocument(wsId, colId)
  const reprocessMut = useReprocessDocument(wsId, colId)
  const assignMut = useAssignDocumentTag(wsId, colId)
  const unassignMut = useUnassignDocumentTag(wsId, colId)
  const createMut = useCreateTag(wsId)
  const tagBusy = assignMut.isPending || unassignMut.isPending || createMut.isPending
  const onErr = (e: Error) => toast.error(e.message)

  const handleCreate = (name: string) =>
    createMut.mutate(
      { name },
      {
        onSuccess: (tag) =>
          assignMut.mutate({ docId: doc.document_id, tagId: tag.id }, { onError: onErr }),
        onError: onErr,
      },
    )

  // Optimistic pre-upload row: the bytes are still being POSTed, so there is no
  // server-side document yet — show just name + an Uploading bar, no actions.
  if (doc.status === 'uploading') {
    return (
      <div className="p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <FileText className="h-7 w-7 shrink-0 text-ink-faint" />
            <div className="min-w-0">
              <p className="truncate text-[13px] font-medium text-ink">{doc.filename}</p>
              <p className="text-xs text-ink-faint">
                {doc.file_type.toUpperCase()} · {formatBytes(doc.file_size)}
              </p>
            </div>
          </div>
          <IngestProgress progress={progress} />
        </div>
      </div>
    )
  }

  return (
    <div className="p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <FileText className="h-7 w-7 shrink-0 text-ink-faint" />
          <div className="min-w-0">
            <p className="truncate text-[13px] font-medium text-ink">{doc.filename}</p>
            <p className="text-xs text-ink-faint">
              {doc.file_type.toUpperCase()} · {formatBytes(doc.file_size)}
              {doc.chunk_count != null &&
                ` · ${doc.chunk_count} chunk${doc.chunk_count === 1 ? '' : 's'}`}{' '}
              · updated {timeAgo(doc.updated_at)}
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
          {failed && canWrite && (
            <button
              type="button"
              disabled={reprocessMut.isPending}
              onClick={() =>
                reprocessMut.mutate(doc.document_id, {
                  onSuccess: () => toast.success(`Reprocessing “${doc.filename}”.`),
                  onError: onErr,
                })
              }
              className="text-xs font-medium text-accent hover:underline disabled:opacity-60"
            >
              {reprocessMut.isPending ? 'Retrying…' : 'Retry'}
            </button>
          )}
          {doc.status === 'pending' || doc.status === 'processing' ? (
            <IngestProgress progress={progress} />
          ) : (
            <StatusBadge status={doc.status ?? 'pending'} />
          )}
          <IndexBadge
            doc={doc}
            busy={indexMut.isPending}
            canWrite={canWrite}
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
          {canWrite && (
            <Button
              variant="ghost"
              size="sm"
              aria-label={`Delete ${doc.filename}`}
              onClick={() => onDelete(doc)}
              className="h-10 w-10 px-0 hover:text-err"
            >
              <Trash2 className="h-7 w-7" />
            </Button>
          )}
        </div>
      </div>
      {((doc.tags ?? []).length > 0 || canManageDocTags) && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {(doc.tags ?? []).map((t) => (
            <TagChip
              key={t.id}
              name={t.name}
              color={t.color}
              onRemove={
                canManageDocTags
                  ? () =>
                      unassignMut.mutate(
                        { docId: doc.document_id, tagId: t.id },
                        { onError: onErr },
                      )
                  : undefined
              }
            />
          ))}
          {canManageDocTags && (
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
          )}
        </div>
      )}
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
  canWrite,
  onIndex,
}: {
  doc: DocumentSummary
  busy: boolean
  canWrite: boolean
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
  // Read-only users see the "Indexed" pill above but not the manual (re)index trigger.
  if (!canWrite) return null
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

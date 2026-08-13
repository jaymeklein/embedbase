import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  CheckCircle2,
  FileText,
  FolderOpen,
  ListChecks,
  Loader2,
  RotateCcw,
  Search,
  SlidersHorizontal,
  X,
  XCircle,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  QueryError,
  Skeleton,
  StatusBadge,
  useToast,
} from '../components/ui'
import type { BadgeStatus } from '../components/ui'
import { apiErrorMessage } from '../i18n/apiError'
import { Pager } from '../components/Pager'
import {
  FILE_TYPES,
  filterInputCls as inputCls,
  filterText as text,
  INGEST_STATUSES as STATUSES,
} from '../components/filters'
import { cn } from '../lib/cn'
import { timeAgo } from '../lib/format'
import { useAuth } from '../auth/AuthContext'
import { useJobs, useJobStats, useReprocessDocument, useRetryFailedJobs } from '../api/hooks'
import type { JobQuery, JobSummary } from '../api/types'
import { useIngestionQueue, type QueueItem } from '../realtime/useIngestionQueue'

/** Jobs fetched per page (matches the server default; ≤ its 200 cap). */
const PAGE_SIZE = 50

/**
 * The ingestion queue: a paginated, filterable history of every ingestion job (from job_records),
 * with live progress streamed onto whichever rows are still in flight.
 *
 * The job list is the backbone (server-paginated + filtered); the `ingestion-queue` WebSocket
 * (useIngestionQueue) overlays a rich live card on each active row and invalidates the list as
 * jobs start and settle, so page 1 stays current without polling.
 */
export default function IngestionQueue() {
  const { t } = useTranslation()
  // A non-admin views the queue scoped to their grants (the backend filters jobs, stats, and the
  // live stream); retrying re-ingests documents, so it stays an admin action.
  const { isAdmin } = useAuth()
  const [page, setPage] = useState(0)
  const [filters, setFiltersState] = useState<JobFilterValues>({})

  const query: JobQuery = { limit: PAGE_SIZE, offset: page * PAGE_SIZE, ...filters }
  const { data, isLoading, isError, error, refetch } = useJobs(query)
  const { items: liveItems, status: conn } = useIngestionQueue()
  const { data: stats } = useJobStats()
  const retryMut = useRetryFailedJobs()
  const toast = useToast()
  const [confirmRetry, setConfirmRetry] = useState(false)

  const jobs = data?.items ?? []
  const total = data?.total ?? 0
  const hasFilters = Object.values(filters).some((v) => v != null && v !== '')
  const failedCount = stats?.counts?.failed ?? 0

  // Bulk retry respects the active filters (the endpoint forces status=failed itself).
  const retryAllFailed = () => {
    retryMut.mutate(filters, {
      onSuccess: ({ retried }) => {
        toast.success(
          retried > 0
            ? t('queue.toast.reenqueued', { count: retried })
            : t('queue.toast.noneMatched'),
        )
        setConfirmRetry(false)
      },
      onError: (e) => toast.error(apiErrorMessage(e, t)),
    })
  }
  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1)
  // A shrinking total (jobs ageing out / filtered) can strand `page` past the end — clamp it so
  // the pager and empty state stay coherent instead of showing an out-of-range window.
  useEffect(() => {
    if (page > lastPage) setPage(lastPage)
  }, [page, lastPage])

  // Live progress keyed by document_id, to enrich the matching (active) job rows.
  const liveById = useMemo(
    () => new Map(liveItems.map((i) => [i.document_id, i])),
    [liveItems],
  )

  const setFilters = (next: JobFilterValues) => {
    setFiltersState(next)
    setPage(0) // the old offset may exceed the new match count
  }

  return (
    <div className="animate-fade-in space-y-6">
      <Header
        connected={conn === 'open'}
        counts={stats?.counts ?? {}}
        pausedSeconds={stats?.paused_seconds ?? 0}
        canRetryAll={isAdmin && failedCount > 0}
        retrying={retryMut.isPending}
        onRetryAll={() => setConfirmRetry(true)}
      />
      <JobFilters value={filters} onChange={setFilters} />
      <QueueBody
        jobs={jobs}
        liveById={liveById}
        filtered={hasFilters}
        isLoading={isLoading}
        isError={isError}
        message={error ? apiErrorMessage(error, t) : undefined}
        onRetry={() => void refetch()}
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
        open={confirmRetry}
        title={t('queue.retryDialog.title')}
        message={
          hasFilters
            ? t('queue.retryDialog.messageFiltered')
            : t('queue.retryDialog.message')
        }
        confirmLabel={t('queue.retryFailed')}
        loading={retryMut.isPending}
        onConfirm={retryAllFailed}
        onClose={() => setConfirmRetry(false)}
      />
    </div>
  )
}

/** Compact "2m 3s" / "1h 4m" / "45s" backoff duration. */
function formatBackoff(seconds: number): string {
  if (seconds >= 3600) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
  if (seconds >= 60) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  return `${seconds}s`
}

/** Real server-side queue totals (they drain as jobs finish), plus the embedding-quota backoff so
 *  the header explains *why* nothing is moving when the provider is rate-limiting. */
function Header({
  connected,
  counts,
  pausedSeconds,
  canRetryAll,
  retrying,
  onRetryAll,
}: {
  connected: boolean
  counts: Record<string, number>
  pausedSeconds: number
  canRetryAll: boolean
  retrying: boolean
  onRetryAll: () => void
}) {
  const { t } = useTranslation()
  const processing = counts.processing ?? 0
  const pending = counts.pending ?? 0
  const waiting = counts.rate_limited ?? 0
  const parts: string[] = []
  if (processing > 0) parts.push(t('queue.parts.ingesting', { count: processing }))
  if (pending > 0) parts.push(t('queue.parts.queued', { count: pending }))
  if (waiting > 0) parts.push(t('queue.parts.waiting', { count: waiting }))
  return (
    <div className="flex items-center justify-between gap-3">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-semibold text-ink">
          <ListChecks className="h-6 w-6 text-accent" />
          {t('nav.ingestionQueue')}
        </h1>
        <p className="mt-1 text-[13px] text-ink-muted">
          {parts.length ? parts.join(' · ') : t('queue.queueEmpty')}
          {pausedSeconds > 0 && (
            <span className="text-warn">
              {' '}
              {t('queue.paused', { time: formatBackoff(pausedSeconds) })}
            </span>
          )}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        {/* Admins only, and only when the queue holds failures. The action re-queues every
            currently-failed document matching the active filters (see retryAllFailed). */}
        {canRetryAll && (
          <Button variant="secondary" size="sm" onClick={onRetryAll} disabled={retrying}>
            <RotateCcw className={cn('h-4 w-4', retrying && 'animate-spin')} />
            {retrying ? t('common.retrying') : t('queue.retryAllFailed')}
          </Button>
        )}
        <span className="flex items-center gap-1.5 text-xs text-ink-faint">
          <span className={cn('h-2 w-2 rounded-full', connected ? 'bg-ok' : 'bg-ink-faint/50')} />
          {connected ? t('queue.live') : t('queue.connecting')}
        </span>
      </div>
    </div>
  )
}

// ── Filters ───────────────────────────────────────────────────────────────────

/** The filter fields the bar edits — the server-side `JobQuery` minus pagination. */
interface JobFilterValues {
  status?: string
  filename?: string
  file_type?: string
  collection?: string
  created_after?: string
  created_before?: string
}

/** Collapsible filter bar for the job history. Primary filters (search, status, type) stay
 *  visible; collection + created range sit under “More”. Emits the whole value object on every
 *  change so the parent can reset pagination to the first page. */
function JobFilters({
  value,
  onChange,
}: {
  value: JobFilterValues
  onChange: (next: JobFilterValues) => void
}) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const set = <K extends keyof JobFilterValues>(key: K, v: JobFilterValues[K]) =>
    onChange({ ...value, [key]: v })
  const active = Object.values(value).some((v) => v !== undefined && v !== '')

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[200px] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint" />
          <input
            className={`${inputCls} w-full pl-8`}
            placeholder={t('documents.filters.searchPlaceholder')}
            value={value.filename ?? ''}
            onChange={(e) => set('filename', text(e.target.value))}
          />
        </div>
        <select
          className={inputCls}
          aria-label={t('documents.filters.statusAria')}
          value={value.status ?? ''}
          onChange={(e) => set('status', text(e.target.value))}
        >
          <option value="">{t('documents.filters.anyStatus')}</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {t(`status.${s}`)}
            </option>
          ))}
        </select>
        <select
          className={inputCls}
          aria-label={t('documents.filters.typeAria')}
          value={value.file_type ?? ''}
          onChange={(e) => set('file_type', text(e.target.value))}
        >
          <option value="">{t('documents.filters.anyType')}</option>
          {FILE_TYPES.map((ft) => (
            <option key={ft} value={ft}>
              {ft}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className={`inline-flex h-9 items-center gap-1.5 rounded-control border px-2.5 text-[13px] transition-colors ${
            expanded ? 'border-accent text-ink' : 'border-border text-ink-muted hover:text-ink'
          }`}
        >
          <SlidersHorizontal className="h-4 w-4" />
          {t('common.more')}
        </button>
        {active && (
          <button
            type="button"
            onClick={() => onChange({})}
            className="inline-flex h-9 items-center gap-1 rounded-control px-2 text-[13px] text-ink-muted hover:text-ink"
          >
            <X className="h-4 w-4" />
            {t('common.clear')}
          </button>
        )}
      </div>

      {expanded && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-control border border-border/60 bg-canvas/50 p-2.5">
          <input
            className={inputCls}
            placeholder={t('queue.filters.collectionPlaceholder')}
            value={value.collection ?? ''}
            onChange={(e) => set('collection', text(e.target.value))}
          />
          <label className="flex items-center gap-1.5 text-xs text-ink-muted">
            {t('documents.filters.created')}
            <input
              type="date"
              className={`${inputCls} w-40`}
              value={value.created_after ?? ''}
              onChange={(e) => set('created_after', text(e.target.value))}
            />
            <span className="text-ink-faint">–</span>
            <input
              type="date"
              className={`${inputCls} w-40`}
              value={value.created_before ?? ''}
              onChange={(e) => set('created_before', text(e.target.value))}
            />
          </label>
        </div>
      )}
    </div>
  )
}

// ── List ──────────────────────────────────────────────────────────────────────

/** Render the queue across its loading / error / empty / data states. Each job renders as a
 *  static row unless it's active *and* the live socket is streaming it — then the rich live card. */
function QueueBody({
  jobs,
  liveById,
  filtered,
  isLoading,
  isError,
  message,
  onRetry,
}: {
  jobs: JobSummary[]
  liveById: Map<string, QueueItem>
  filtered: boolean
  isLoading: boolean
  isError: boolean
  message?: string
  onRetry: () => void
}) {
  const { t } = useTranslation()
  if (isLoading) {
    return (
      <div className="space-y-3">
        {[0, 1, 2].map((i) => (
          <Card key={i} className="flex items-center justify-between p-4">
            <Skeleton className="h-4 w-56" />
            <Skeleton className="h-5 w-20 rounded-full" />
          </Card>
        ))}
      </div>
    )
  }
  if (isError) {
    return <QueryError title={t('queue.error')} message={message} onRetry={onRetry} />
  }
  if (jobs.length === 0) {
    return filtered ? (
      <EmptyState
        icon={<ListChecks className="h-7 w-7" />}
        title={t('queue.empty.filtered.title')}
        description={t('queue.empty.filtered.description')}
      />
    ) : (
      <EmptyState
        icon={<ListChecks className="h-7 w-7" />}
        title={t('queue.empty.none.title')}
        description={t('queue.empty.none.description')}
      />
    )
  }
  // Jobs arrive newest-first. Overlay the live stream on at most one row per document — the newest
  // attempt — and only while the live item is itself mid-flight. So a settled item collapses to the
  // compact static row at once (no big "done" card wedged open if the refetch lags), and a stale
  // earlier attempt for a re-ingested document can't borrow the current run's card.
  const overlaid = new Set<string>()
  return (
    <div className="space-y-3">
      {jobs.map((job) => {
        const live = liveById.get(job.document_id)
        if (
          live &&
          live.status !== 'done' &&
          live.status !== 'failed' &&
          !overlaid.has(job.document_id)
        ) {
          overlaid.add(job.document_id)
          return <QueueCard key={job.job_id} item={live} />
        }
        return <JobRow key={job.job_id} job={job} />
      })}
    </div>
  )
}

/** Link from a queue row to its file in the collection (pre-filtered to the filename) so the user
 *  can edit or delete it there. Hidden when the collection is gone — there's no workspace to open. */
function OpenInCollection({
  workspaceId,
  collectionId,
  filename,
}: {
  workspaceId: string | null
  collectionId: string
  filename: string
}) {
  const { t } = useTranslation()
  if (!workspaceId) return null
  return (
    <Link
      to={`/workspaces/${workspaceId}/collections/${collectionId}?filename=${encodeURIComponent(filename)}`}
      title={t('queue.openInCollectionTitle')}
      className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-ink-muted hover:text-ink"
    >
      <FolderOpen className="h-3.5 w-3.5" />
      {t('queue.open')}
    </Link>
  )
}

/** A static queue row for a job with no live stream (finished, failed, or not currently in the
 *  live buffer). The failure reason ships in the row payload, so no extra request is needed. */
function JobRow({ job }: { job: JobSummary }) {
  const { t } = useTranslation()
  const { isAdmin } = useAuth()
  const [showError, setShowError] = useState(false)
  const failed = job.status === 'failed'
  const toast = useToast()
  const reprocessMut = useReprocessDocument()
  return (
    <Card className="space-y-3 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2">
          <FileText className="mt-0.5 h-4 w-4 shrink-0 text-ink-faint" />
          <div className="min-w-0">
            <p className="truncate text-[13px] font-medium text-ink">{job.filename}</p>
            <p className="truncate text-xs text-ink-faint">
              {job.collection_name ?? t('queue.unknownCollection')} · {job.file_type.toUpperCase()}
              {job.chunk_count != null &&
                ` · ${t('documents.row.chunk', { count: job.chunk_count })}`}{' '}
              · {timeAgo(job.updated_at)}
            </p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <OpenInCollection
            workspaceId={job.workspace_id}
            collectionId={job.collection_id}
            filename={job.filename}
          />
          {failed && job.error && (
            <button
              type="button"
              onClick={() => setShowError((v) => !v)}
              className="text-xs font-medium text-err hover:underline"
            >
              {showError ? t('documents.row.hide') : t('documents.row.why')}
            </button>
          )}
          {failed && isAdmin && (
            <button
              type="button"
              disabled={reprocessMut.isPending}
              onClick={() =>
                reprocessMut.mutate(job.document_id, {
                  onSuccess: () => toast.success(t('documents.toast.reprocessing', { name: job.filename })),
                  onError: (e) => toast.error(apiErrorMessage(e, t)),
                })
              }
              className="text-xs font-medium text-accent hover:underline disabled:opacity-60"
            >
              {reprocessMut.isPending ? t('common.retrying') : t('common.retry')}
            </button>
          )}
          <StatusBadge status={job.status as BadgeStatus} />
        </div>
      </div>
      {failed && showError && job.error && (
        <p className="rounded-control border border-err/40 bg-err/10 px-3 py-2 text-xs text-err">
          {job.error}
        </p>
      )}
    </Card>
  )
}

// ── Live card (streamed active rows) ────────────────────────────────────────────

const PHASE_STYLE: Record<string, string> = {
  parsing: 'text-warn',
  embedding: 'text-accent',
  storing: 'text-accent',
  rate_limited: 'text-warn',
  done: 'text-ok',
  failed: 'text-err',
}

function QueueCard({ item }: { item: QueueItem }) {
  const { t } = useTranslation()
  const determinate = item.pct != null
  const terminal = item.status === 'done' || item.status === 'failed'
  return (
    <Card className="space-y-3 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2">
          <FileText className="mt-0.5 h-4 w-4 shrink-0 text-ink-faint" />
          <div className="min-w-0">
            <p className="truncate text-[13px] font-medium text-ink">{item.filename}</p>
            <p className="truncate text-xs text-ink-faint">{item.collection_name}</p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <OpenInCollection
            workspaceId={item.workspace_id}
            collectionId={item.collection_id}
            filename={item.filename}
          />
          <PhaseBadge item={item} />
        </div>
      </div>

      {item.status === 'failed' && item.error && (
        <p className="rounded-control border border-err/40 bg-err/10 px-3 py-2 text-xs text-err">
          {item.error}
        </p>
      )}

      {!terminal && (
        <div className="space-y-1">
          <div className="flex items-center justify-between text-xs text-ink-muted">
            {item.status === 'rate_limited' && item.retry_at ? (
              <RetryCountdown retryAt={item.retry_at} />
            ) : (
              <span className="capitalize">{t(`queue.phase.${item.phase}`)}</span>
            )}
            {item.total != null && (
              <span className="tabular-nums">
                {t('queue.chunkProgress', { current: item.current ?? 0, total: item.total })}
              </span>
            )}
          </div>
          <div className="relative h-1.5 overflow-hidden rounded-full bg-canvas">
            {determinate ? (
              <div
                className={`h-full rounded-full transition-[width] duration-300 ${item.status === 'rate_limited' ? 'bg-warn' : 'bg-accent'}`}
                style={{ width: `${item.pct}%` }}
              />
            ) : (
              <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-accent/70 to-transparent" />
            )}
          </div>
        </div>
      )}

      {item.recentChunks.length > 0 && (
        <div className="rounded-control border border-border bg-canvas/50">
          <div className="border-b border-border px-3 py-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-faint">
            {t('queue.chunks')} {terminal ? '' : t('queue.chunksLatest')}
          </div>
          <ul className="max-h-48 overflow-y-auto py-1">
            {[...item.recentChunks].reverse().map((c) => (
              <li
                key={c.index}
                className="flex items-baseline gap-2 px-3 py-0.5 text-xs text-ink-muted"
              >
                <span className="shrink-0 tabular-nums text-ink-faint">#{c.index}</span>
                <span className="truncate">{c.label || '—'}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  )
}

function PhaseBadge({ item }: { item: QueueItem }) {
  const { t } = useTranslation()
  if (item.status === 'rate_limited') {
    return (
      <Badge className="shrink-0 text-warn">
        <AlertTriangle className="h-3.5 w-3.5" />
        {t('status.rate_limited')}
      </Badge>
    )
  }
  const cls = PHASE_STYLE[item.phase] ?? 'text-ink-muted'
  const icon =
    item.status === 'done' ? (
      <CheckCircle2 className="h-3.5 w-3.5" />
    ) : item.status === 'failed' ? (
      <XCircle className="h-3.5 w-3.5" />
    ) : (
      <Loader2 className="h-3.5 w-3.5 animate-spin" />
    )
  return (
    <Badge className={cn('shrink-0 capitalize', cls)}>
      {icon}
      {item.status === 'processing' ? t(`queue.phase.${item.phase}`) : t(`status.${item.status}`)}
    </Badge>
  )
}

/** Live "retry in M:SS" countdown to a paused item's scheduled resume time. */
function RetryCountdown({ retryAt }: { retryAt: string }) {
  const { t } = useTranslation()
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  const remaining = Math.max(0, Math.round((new Date(retryAt).getTime() - now) / 1000))
  const label =
    remaining > 0
      ? t('queue.retryIn', {
          time: `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}`,
        })
      : t('queue.retrying')
  return <span className="tabular-nums text-warn">{label}</span>
}

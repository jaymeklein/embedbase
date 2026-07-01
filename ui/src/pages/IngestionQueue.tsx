import { AlertTriangle, CheckCircle2, FileText, ListChecks, Loader2, XCircle } from 'lucide-react'
import { Badge, Card, EmptyState } from '../components/ui'
import { cn } from '../lib/cn'
import { useIngestionQueue, type QueueItem } from '../realtime/useIngestionQueue'

/** Live, global view of documents being ingested — each file with its chunks as they land. */
export default function IngestionQueue() {
  const { items, status } = useIngestionQueue()
  const active = items.filter((i) => i.status === 'processing').length

  return (
    <div className="animate-fade-in space-y-6">
      <Header connected={status === 'open'} active={active} />
      {items.length === 0 ? (
        <EmptyState
          icon={<ListChecks className="h-7 w-7" />}
          title="No ingestions yet"
          description="Upload a document and its chunks will stream here in real time as they're embedded."
        />
      ) : (
        <div className="space-y-3">
          {items.map((item) => (
            <QueueCard key={item.document_id} item={item} />
          ))}
        </div>
      )}
    </div>
  )
}

function Header({ connected, active }: { connected: boolean; active: number }) {
  return (
    <div className="flex items-center justify-between">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-semibold text-ink">
          <ListChecks className="h-6 w-6 text-accent" />
          Ingestion Queue
        </h1>
        <p className="mt-1 text-[13px] text-ink-muted">
          {active > 0 ? `${active} document${active === 1 ? '' : 's'} ingesting` : 'No active ingestions'}{' '}
          · chunks stream in live as they're embedded.
        </p>
      </div>
      <span className="flex items-center gap-1.5 text-xs text-ink-faint">
        <span
          className={cn('h-2 w-2 rounded-full', connected ? 'bg-ok' : 'bg-ink-faint/50')}
        />
        {connected ? 'Live' : 'Connecting…'}
      </span>
    </div>
  )
}

const PHASE_STYLE: Record<string, string> = {
  parsing: 'text-warn',
  embedding: 'text-accent',
  storing: 'text-accent',
  done: 'text-ok',
  failed: 'text-err',
}

function QueueCard({ item }: { item: QueueItem }) {
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
        <PhaseBadge item={item} />
      </div>

      {item.status === 'failed' && item.error && (
        <p className="rounded-control border border-err/40 bg-err/10 px-3 py-2 text-xs text-err">
          {item.error}
        </p>
      )}
      {item.stalled && (
        <p className="rounded-control border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-ink-muted">
          Interrupted — no progress at {item.current ?? 0}/{item.total} for a while. The worker
          likely stopped or restarted; re-ingest the file to resume.
        </p>
      )}

      {!terminal && (
        <div className="space-y-1">
          <div className="flex items-center justify-between text-xs text-ink-muted">
            <span className="capitalize">{item.stalled ? 'stalled' : item.phase}</span>
            {item.total != null && (
              <span className="tabular-nums">
                {item.current ?? 0} / {item.total} chunks
              </span>
            )}
          </div>
          <div className="relative h-1.5 overflow-hidden rounded-full bg-canvas">
            {determinate ? (
              <div
                className={`h-full rounded-full transition-[width] duration-300 ${item.stalled ? 'bg-warn' : 'bg-accent'}`}
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
            Chunks {terminal ? '' : '(latest)'}
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
  if (item.stalled) {
    return (
      <Badge className="shrink-0 text-warn">
        <AlertTriangle className="h-3.5 w-3.5" />
        Interrupted
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
      {item.status === 'processing' ? item.phase : item.status}
    </Badge>
  )
}

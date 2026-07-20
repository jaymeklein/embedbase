import { useState } from 'react'
import { AlertTriangle, DatabaseZap, RefreshCw } from 'lucide-react'
import { useIndexStatus, useRetryFailedJobs } from '../api/hooks'
import type { CollectionIndexStatus } from '../api/types'
import {
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  QueryError,
  Skeleton,
  useToast,
} from '../components/ui'

/** Keyword-index coverage per workspace → collection, with a retry for failed ingestions. */
export default function Indexing() {
  const { data, isLoading, isError, error, refetch } = useIndexStatus()

  if (isLoading) {
    return (
      <div className="animate-fade-in space-y-6">
        <Header />
        <Card className="space-y-3 p-4">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-12 w-full rounded-control" />
          ))}
        </Card>
      </div>
    )
  }
  if (isError) {
    return (
      <div className="animate-fade-in space-y-6">
        <Header />
        <QueryError
          title="Could not load index status"
          message={error?.message}
          onRetry={() => void refetch()}
        />
      </div>
    )
  }
  const workspaces = data?.workspaces ?? []
  return (
    <div className="animate-fade-in space-y-6">
      <Header />
      {workspaces.length === 0 ? (
        <EmptyState
          icon={<DatabaseZap className="h-7 w-7" />}
          title="Nothing to index yet"
          description="Upload documents to a collection and they'll appear here."
        />
      ) : (
        <div className="space-y-6">
          {workspaces.map((ws) => (
            <section key={ws.workspace_id} className="space-y-2">
              <h2 className="px-1 text-xs font-semibold uppercase tracking-wide text-ink-faint">
                {ws.workspace_name}
              </h2>
              <Card className="divide-y divide-border">
                {ws.collections.map((col) => (
                  <CollectionRow key={col.collection_id} col={col} />
                ))}
              </Card>
            </section>
          ))}
        </div>
      )}
    </div>
  )
}

function Header() {
  return (
    <header>
      <h1 className="text-xl font-semibold tracking-tight text-ink">Indexing</h1>
      <p className="mt-1 text-[13px] text-ink-muted">
        Keyword-index coverage per collection. A document is searchable once its chunks are
        stored — the keyword index is maintained automatically, so there is nothing to rebuild.
        Anything short of full coverage is an ingestion that has not finished; retry the failed
        ones here.
      </p>
    </header>
  )
}

/**
 * One collection: coverage bar, counts, and — when something actually failed — a confirmed retry.
 *
 * There is deliberately no "re-index" action: the keyword index is a generated column that
 * Postgres maintains on write, so a rebuild is a no-op (see api/services/indexing.py). A gap in
 * coverage is always an unfinished ingestion, so the only useful action is re-ingesting the failed
 * documents — which re-embeds them, hence the confirmation. In-flight ones are left to finish.
 */
function CollectionRow({ col }: { col: CollectionIndexStatus }) {
  const toast = useToast()
  const retryMut = useRetryFailedJobs()
  const [confirming, setConfirming] = useState(false)
  const pct = col.total > 0 ? Math.round((col.indexed / col.total) * 100) : 100
  const fullyIndexed = col.unindexed === 0
  const failedDocs = `${col.failed} failed document${col.failed === 1 ? '' : 's'}`

  const retryFailed = () =>
    retryMut.mutate(
      { collection_id: col.collection_id },
      {
        onSuccess: ({ retried }) => {
          toast.success(`Re-queued ${retried} document${retried === 1 ? '' : 's'}.`)
          setConfirming(false)
        },
        // Leave the dialog open on failure so it can be retried from there.
        onError: (e) => toast.error(e.message),
      },
    )

  return (
    <>
      <div className="flex items-center justify-between gap-4 p-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-[13px] font-medium text-ink">{col.collection_name}</p>
            {!fullyIndexed && (
              <span className="inline-flex items-center gap-1 rounded-full border border-warn/40 bg-warn/5 px-2 py-0.5 text-xs font-medium text-warn">
                <AlertTriangle className="h-3.5 w-3.5" />
                {col.unindexed} unindexed
              </span>
            )}
          </div>
          <div className="mt-2 flex items-center gap-3">
            <div className="h-1.5 w-40 overflow-hidden rounded-full bg-canvas">
              <div
                className={fullyIndexed ? 'h-full bg-ok' : 'h-full bg-warn'}
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className="text-xs text-ink-faint">
              {col.indexed}/{col.total} indexed
              {col.pending > 0 && ` · ${col.pending} ingesting`}
              {col.failed > 0 && ` · ${col.failed} failed`}
            </span>
          </div>
        </div>
        {/* Only offer the retry when there is something stuck to retry: a document still
            ingesting will finish on its own, and a fully-indexed collection has nothing to do. */}
        {col.failed > 0 && (
          <Button
            variant="secondary"
            size="sm"
            loading={retryMut.isPending}
            onClick={() => setConfirming(true)}
          >
            <RefreshCw className="h-4 w-4" />
            Retry failed
          </Button>
        )}
      </div>

      <ConfirmDialog
        open={confirming}
        title="Retry failed ingestions"
        message={`Re-ingest the ${failedDocs} in “${col.collection_name}”? Each is re-queued and embedded again from its stored file — resuming where the last attempt stopped — so this spends embedding-provider quota. Documents still ingesting are left alone.`}
        confirmLabel="Retry failed"
        loading={retryMut.isPending}
        onConfirm={retryFailed}
        onClose={() => setConfirming(false)}
      />
    </>
  )
}

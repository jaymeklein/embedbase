import { useState } from 'react'
import { AlertTriangle, DatabaseZap, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../auth/AuthContext'
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
import { apiErrorMessage } from '../i18n/apiError'

/** Keyword-index coverage per workspace → collection, with a retry for failed ingestions. */
export default function Indexing() {
  const { t } = useTranslation()
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
          title={t('indexing.error')}
          message={error ? apiErrorMessage(error, t) : undefined}
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
          title={t('indexing.empty.title')}
          description={t('indexing.empty.description')}
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
  const { t } = useTranslation()
  return (
    <header>
      <h1 className="text-xl font-semibold tracking-tight text-ink">{t('indexing.title')}</h1>
      <p className="mt-1 text-[13px] text-ink-muted">
        {t('indexing.subtitle')}
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
  const { t } = useTranslation()
  // A non-admin sees coverage for their permitted collections but can't retry (re-ingest).
  const { isAdmin } = useAuth()
  const toast = useToast()
  const retryMut = useRetryFailedJobs()
  const [confirming, setConfirming] = useState(false)
  const pct = col.total > 0 ? Math.round((col.indexed / col.total) * 100) : 100
  const fullyIndexed = col.unindexed === 0
  const failedDocs = t('indexing.failedDocs', { count: col.failed })

  const retryFailed = () =>
    retryMut.mutate(
      { collection_id: col.collection_id },
      {
        onSuccess: ({ retried }) => {
          toast.success(t('indexing.toast.requeued', { count: retried }))
          setConfirming(false)
        },
        // Leave the dialog open on failure so it can be retried from there.
        onError: (e) => toast.error(apiErrorMessage(e, t)),
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
                {t('indexing.unindexed', { count: col.unindexed })}
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
              {t('indexing.coverage', { indexed: col.indexed, total: col.total })}
              {col.pending > 0 && t('indexing.pendingSuffix', { count: col.pending })}
              {col.failed > 0 && t('indexing.failedSuffix', { count: col.failed })}
            </span>
          </div>
        </div>
        {/* Admins only, and only when there is something stuck to retry: a document still
            ingesting will finish on its own, and a fully-indexed collection has nothing to do. */}
        {isAdmin && col.failed > 0 && (
          <Button
            variant="secondary"
            size="sm"
            loading={retryMut.isPending}
            onClick={() => setConfirming(true)}
          >
            <RefreshCw className="h-4 w-4" />
            {t('indexing.retryFailed')}
          </Button>
        )}
      </div>

      <ConfirmDialog
        open={confirming}
        title={t('indexing.retryDialog.title')}
        message={t('indexing.retryDialog.message', {
          docs: failedDocs,
          name: col.collection_name,
        })}
        confirmLabel={t('indexing.retryFailed')}
        loading={retryMut.isPending}
        onConfirm={retryFailed}
        onClose={() => setConfirming(false)}
      />
    </>
  )
}

import { Link } from 'react-router-dom'
import { FileClock, FileText, FolderKanban, Layers } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useHealth, useRecentDocuments, useWorkspaces } from '../api/hooks'
import type { Workspace } from '../api/types'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  EntityIcon,
  QueryError,
  Skeleton,
  StatusBadge,
} from '../components/ui'
import { apiErrorMessage } from '../i18n/apiError'
import { cn } from '../lib/cn'
import { formatUptime, timeAgo } from '../lib/format'

/** Operator landing page: system health, workspace overview, recent activity. */
export default function Dashboard() {
  const { t } = useTranslation()
  return (
    <div className="animate-fade-in space-y-8">
      <header>
        <h1 className="text-xl font-semibold tracking-tight text-ink">{t('dashboard.title')}</h1>
        <p className="mt-1 text-[13px] text-ink-muted">
          {t('dashboard.subtitle')}
        </p>
      </header>
      <HealthCard />
      <WorkspaceOverview />
      <RecentActivity />
    </div>
  )
}

/** One labelled metric inside the health card, with an optional liveness dot. */
function StatItem({ label, value, online }: { label: string; value: string; online?: boolean }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-ink-faint">{label}</span>
      <span className="flex items-center gap-1.5 font-mono text-[13px] text-ink">
        {online !== undefined && (
          <span className={cn('h-1.5 w-1.5 rounded-full', online ? 'bg-ok' : 'bg-err')} />
        )}
        <span className="truncate">{value}</span>
      </span>
    </div>
  )
}

/** `GET /healthz` summary card. */
function HealthCard() {
  const { t } = useTranslation()
  const { data, isLoading, isError, error, refetch } = useHealth()
  if (isLoading) return <Skeleton className="h-36 w-full rounded-card" />
  if (isError || !data) {
    return (
      <QueryError
        title={t('auth.api.unreachable')}
        message={error ? apiErrorMessage(error, t) : t('dashboard.health.unreachable')}
        onRetry={() => void refetch()}
      />
    )
  }
  const healthy = ['ok', 'healthy'].includes(data.status.toLowerCase())
  return (
    <Card className="p-5">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink">{t('dashboard.health.system')}</h2>
        <span className="inline-flex items-center gap-1.5 text-xs">
          <span className={cn('h-1.5 w-1.5 rounded-full', healthy ? 'bg-ok' : 'bg-warn')} />
          <span className={healthy ? 'text-ok' : 'text-warn'}>{data.status}</span>
        </span>
      </div>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatItem
          label={t('dashboard.health.vectorStore')}
          value={data.vector_store}
          online={data.vector_store_connected}
        />
        <StatItem
          label={t('documents.filters.embeddingModel')}
          value={data.embedding_model}
          online={data.embedding_model_loaded}
        />
        <StatItem label={t('dashboard.health.version')} value={data.version} />
        <StatItem label={t('dashboard.health.uptime')} value={formatUptime(data.uptime_seconds)} />
      </div>
      <p className="mt-4 text-xs text-ink-faint">
        {data.embedding_provider} · {data.service}
      </p>
    </Card>
  )
}

/** Workspace count plus a compact grid of clickable workspace cards. */
function WorkspaceOverview() {
  const { t } = useTranslation()
  const { data, isLoading, isError, error, refetch } = useWorkspaces()
  return (
    <section>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink">
          {t('workspaces.title')}
          {data && <span className="ml-1 font-normal text-ink-faint">· {data.length}</span>}
        </h2>
        <Link to="/workspaces" className="text-xs font-medium text-accent hover:underline">
          {t('dashboard.viewAll')}
        </Link>
      </div>
      <WorkspaceGrid
        data={data}
        isLoading={isLoading}
        isError={isError}
        message={error ? apiErrorMessage(error, t) : undefined}
        onRetry={() => void refetch()}
      />
    </section>
  )
}

/** Render the workspace grid across its loading / error / empty / data states. */
function WorkspaceGrid({
  data,
  isLoading,
  isError,
  message,
  onRetry,
}: {
  data: Workspace[] | undefined
  isLoading: boolean
  isError: boolean
  message?: string
  onRetry: () => void
}) {
  const { t } = useTranslation()
  if (isLoading) {
    return (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-20 rounded-card" />
        ))}
      </div>
    )
  }
  if (isError) return <QueryError title={t('workspaces.error')} message={message} onRetry={onRetry} />
  if (!data || data.length === 0) {
    return (
      <EmptyState
        icon={<FolderKanban className="h-7 w-7" />}
        title={t('workspaces.empty.title')}
        description={t('workspaces.empty.canCreate')}
        action={
          <Link to="/workspaces">
            <Button size="sm">{t('dashboard.goToWorkspaces')}</Button>
          </Link>
        }
      />
    )
  }
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {data.map((ws) => (
        <WorkspaceCard key={ws.id} ws={ws} />
      ))}
    </div>
  )
}

/** A single workspace card that links through to its collections. */
function WorkspaceCard({ ws }: { ws: Workspace }) {
  const { t } = useTranslation()
  return (
    <Link to={`/workspaces/${ws.id}`}>
      <Card interactive className="flex h-full items-start gap-3 p-4">
        <span
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-control"
          style={{ backgroundColor: `${ws.color}1A`, color: ws.color }}
        >
          <EntityIcon name={ws.icon} className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <h3 className="truncate text-sm font-medium text-ink">{ws.name}</h3>
            {ws.collection_count !== undefined && (
              <Badge>
                <Layers className="h-3.5 w-3.5" />
                {ws.collection_count}
              </Badge>
            )}
          </div>
          <p className="mt-0.5 truncate text-xs text-ink-muted">
            {ws.description || t('common.noDescription')}
          </p>
        </div>
      </Card>
    </Link>
  )
}

/** Most-recently-updated documents aggregated across every collection. */
function RecentActivity() {
  const { t } = useTranslation()
  const { documents, isLoading } = useRecentDocuments()
  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold text-ink">{t('dashboard.recentActivity')}</h2>
      <RecentActivityBody documents={documents} isLoading={isLoading} />
    </section>
  )
}

function RecentActivityBody({
  documents,
  isLoading,
}: {
  documents: ReturnType<typeof useRecentDocuments>['documents']
  isLoading: boolean
}) {
  const { t } = useTranslation()
  if (isLoading && documents.length === 0) {
    return (
      <Card className="divide-y divide-border">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex items-center justify-between p-4">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-5 w-20 rounded-full" />
          </div>
        ))}
      </Card>
    )
  }
  if (documents.length === 0) {
    return (
      <EmptyState
        icon={<FileClock className="h-7 w-7" />}
        title={t('dashboard.noActivity.title')}
        description={t('dashboard.noActivity.description')}
      />
    )
  }
  return (
    <Card className="divide-y divide-border">
      {documents.map((d) => (
        <Link
          key={`${d.collection_id}:${d.document_id}`}
          to={`/workspaces/${d.workspace_id}/collections/${d.collection_id}`}
          className="flex items-center justify-between gap-3 p-4 transition-colors hover:bg-canvas"
        >
          <div className="flex min-w-0 items-center gap-3">
            <FileText className="h-5 w-5 shrink-0 text-ink-faint" />
            <div className="min-w-0">
              <p className="truncate text-[13px] font-medium text-ink">{d.filename}</p>
              <p className="text-xs text-ink-faint">{timeAgo(d.updated_at)}</p>
            </div>
          </div>
          <StatusBadge status={d.status ?? 'pending'} />
        </Link>
      ))}
    </Card>
  )
}

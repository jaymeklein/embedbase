import { Link } from 'react-router-dom'
import { FileClock, FileText, FolderKanban, Layers } from 'lucide-react'
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
import { cn } from '../lib/cn'
import { formatUptime, timeAgo } from '../lib/format'

/** Operator landing page: system health, workspace overview, recent activity. */
export default function Dashboard() {
  return (
    <div className="animate-fade-in space-y-8">
      <header>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Dashboard</h1>
        <p className="mt-1 text-[13px] text-ink-muted">
          System health and recent activity at a glance.
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
  const { data, isLoading, isError, error, refetch } = useHealth()
  if (isLoading) return <Skeleton className="h-36 w-full rounded-card" />
  if (isError || !data) {
    return (
      <QueryError
        title="API unreachable"
        message={error?.message ?? 'Could not reach the health endpoint.'}
        onRetry={() => void refetch()}
      />
    )
  }
  const healthy = ['ok', 'healthy'].includes(data.status.toLowerCase())
  return (
    <Card className="p-5">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink">System</h2>
        <span className="inline-flex items-center gap-1.5 text-xs">
          <span className={cn('h-1.5 w-1.5 rounded-full', healthy ? 'bg-ok' : 'bg-warn')} />
          <span className={healthy ? 'text-ok' : 'text-warn'}>{data.status}</span>
        </span>
      </div>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatItem
          label="Vector store"
          value={data.vector_store}
          online={data.vector_store_connected}
        />
        <StatItem
          label="Embedding model"
          value={data.embedding_model}
          online={data.embedding_model_loaded}
        />
        <StatItem label="Version" value={data.version} />
        <StatItem label="Uptime" value={formatUptime(data.uptime_seconds)} />
      </div>
      <p className="mt-4 text-xs text-ink-faint">
        {data.embedding_provider} · {data.service}
      </p>
    </Card>
  )
}

/** Workspace count plus a compact grid of clickable workspace cards. */
function WorkspaceOverview() {
  const { data, isLoading, isError, error, refetch } = useWorkspaces()
  return (
    <section>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink">
          Workspaces
          {data && <span className="ml-1 font-normal text-ink-faint">· {data.length}</span>}
        </h2>
        <Link to="/workspaces" className="text-xs font-medium text-accent hover:underline">
          View all
        </Link>
      </div>
      <WorkspaceGrid
        data={data}
        isLoading={isLoading}
        isError={isError}
        message={error?.message}
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
  if (isLoading) {
    return (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-20 rounded-card" />
        ))}
      </div>
    )
  }
  if (isError) return <QueryError title="Could not load workspaces" message={message} onRetry={onRetry} />
  if (!data || data.length === 0) {
    return (
      <EmptyState
        icon={<FolderKanban className="h-6 w-6" />}
        title="No workspaces yet"
        description="Create your first workspace to start organising collections."
        action={
          <Link to="/workspaces">
            <Button size="sm">Go to Workspaces</Button>
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
  return (
    <Link to={`/workspaces/${ws.id}`}>
      <Card interactive className="flex h-full items-start gap-3 p-4">
        <span
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-control"
          style={{ backgroundColor: `${ws.color}1A`, color: ws.color }}
        >
          <EntityIcon name={ws.icon} className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <h3 className="truncate text-sm font-medium text-ink">{ws.name}</h3>
            {ws.collection_count !== undefined && (
              <Badge>
                <Layers className="h-3 w-3" />
                {ws.collection_count}
              </Badge>
            )}
          </div>
          <p className="mt-0.5 truncate text-xs text-ink-muted">
            {ws.description || 'No description'}
          </p>
        </div>
      </Card>
    </Link>
  )
}

/** Most-recently-updated documents aggregated across every collection. */
function RecentActivity() {
  const { documents, isLoading } = useRecentDocuments()
  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold text-ink">Recent activity</h2>
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
        icon={<FileClock className="h-6 w-6" />}
        title="No recent activity"
        description="Document ingestion across your collections will show up here."
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
            <FileText className="h-4 w-4 shrink-0 text-ink-faint" />
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

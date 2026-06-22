import { useState } from 'react'
import { Lock } from 'lucide-react'
import { useHealth } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'
import { Button, Card, QueryError, Skeleton } from '../components/ui'
import { ConfigPanel } from '../components/settings/ConfigPanel'
import { McpPanel } from '../components/settings/McpPanel'
import { cn } from '../lib/cn'
import { formatUptime } from '../lib/format'

const TABS = [
  { id: 'general', label: 'General' },
  { id: 'mcp', label: 'MCP server' },
] as const
type TabId = (typeof TABS)[number]['id']

/** Settings: a General tab (runtime, config, security) and an MCP server tab. */
export default function Settings() {
  const [tab, setTab] = useState<TabId>('general')
  return (
    <div className="animate-fade-in space-y-6">
      <header>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Settings</h1>
        <p className="mt-1 text-[13px] text-ink-muted">
          Runtime configuration, console security, and MCP access.
        </p>
      </header>

      <div className="flex gap-1 border-b border-border" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              '-mb-px border-b-2 px-3 py-2 text-[13px] font-medium transition-colors',
              tab === t.id
                ? 'border-accent text-ink'
                : 'border-transparent text-ink-muted hover:text-ink',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'general' ? <GeneralTab /> : <McpPanel />}
    </div>
  )
}

/** The original settings content: runtime snapshot, config form, and security. */
function GeneralTab() {
  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-ink">Runtime</h2>
        <RuntimeSummary />
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-ink">Configuration</h2>
        <ConfigPanel />
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-ink">Security</h2>
        <SecurityPanel />
      </section>
    </div>
  )
}

/** Read-only snapshot of the live config the API reports at `/healthz`. */
function RuntimeSummary() {
  const { data, isLoading, isError, error, refetch } = useHealth()
  if (isLoading) return <Skeleton className="h-40 w-full rounded-card" />
  if (isError || !data) {
    return (
      <QueryError
        title="API unreachable"
        message={error?.message ?? 'Could not reach the health endpoint.'}
        onRetry={() => void refetch()}
      />
    )
  }
  return (
    <Card className="grid grid-cols-1 gap-x-8 gap-y-4 p-5 sm:grid-cols-2">
      <Row label="Service" value={data.service} />
      <Row label="Version" value={data.version} />
      <Row label="Vector store" value={data.vector_store} online={data.vector_store_connected} />
      <Row label="Embedding provider" value={data.embedding_provider} />
      <Row label="Embedding model" value={data.embedding_model} online={data.embedding_model_loaded} />
      <Row label="Uptime" value={formatUptime(data.uptime_seconds)} />
    </Card>
  )
}

/** One labelled config value with an optional connected/loaded dot. */
function Row({ label, value, online }: { label: string; value: string; online?: boolean }) {
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

/** Master-key note plus a Lock action mirroring the Topbar. */
function SecurityPanel() {
  const { lock } = useAuth()
  return (
    <Card className="flex items-center justify-between gap-4 p-5">
      <div>
        <p className="text-[13px] text-ink">Master key</p>
        <p className="mt-0.5 text-xs text-ink-muted">
          Held in this browser only. Lock to clear it and return to the unlock screen.
        </p>
      </div>
      <Button variant="secondary" onClick={lock}>
        <Lock className="h-4 w-4" />
        Lock console
      </Button>
    </Card>
  )
}

import { useState } from 'react'
import { Lock } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useHealth } from '../api/hooks'
import { useAuth } from '../auth/AuthContext'
import { Button, Card, QueryError, Skeleton } from '../components/ui'
import { ConfigPanel } from '../components/settings/ConfigPanel'
import { McpPanel } from '../components/settings/McpPanel'
import { cn } from '../lib/cn'
import { formatUptime } from '../lib/format'

const TABS = [
  { id: 'general', labelKey: 'settings.tabs.general' },
  { id: 'mcp', labelKey: 'settings.tabs.mcp' },
] as const
type TabId = (typeof TABS)[number]['id']

/** Settings: a General tab (runtime, config, security) and an MCP server tab. */
export default function Settings() {
  const { t } = useTranslation()
  const [tab, setTab] = useState<TabId>('general')
  return (
    <div className="animate-fade-in space-y-6">
      <header>
        <h1 className="text-xl font-semibold tracking-tight text-ink">{t('settings.title')}</h1>
        <p className="mt-1 text-[13px] text-ink-muted">{t('settings.subtitle')}</p>
      </header>

      <div className="flex gap-1 border-b border-border" role="tablist">
        {TABS.map((item) => (
          <button
            key={item.id}
            role="tab"
            aria-selected={tab === item.id}
            onClick={() => setTab(item.id)}
            className={cn(
              '-mb-px border-b-2 px-3 py-2 text-[13px] font-medium transition-colors',
              tab === item.id
                ? 'border-accent text-ink'
                : 'border-transparent text-ink-muted hover:text-ink',
            )}
          >
            {t(item.labelKey)}
          </button>
        ))}
      </div>

      {tab === 'general' ? <GeneralTab /> : <McpPanel />}
    </div>
  )
}

/** The original settings content: runtime snapshot, config form, and security. */
function GeneralTab() {
  const { t } = useTranslation()
  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-ink">{t('settings.runtime.title')}</h2>
        <RuntimeSummary />
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-ink">{t('settings.config.title')}</h2>
        <ConfigPanel />
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-ink">{t('settings.security.title')}</h2>
        <SecurityPanel />
      </section>
    </div>
  )
}

/** Read-only snapshot of the live config the API reports at `/healthz`. */
function RuntimeSummary() {
  const { t } = useTranslation()
  const { data, isLoading, isError, error, refetch } = useHealth()
  if (isLoading) return <Skeleton className="h-40 w-full rounded-card" />
  if (isError || !data) {
    return (
      <QueryError
        title={t('settings.runtime.unreachable')}
        message={error?.message ?? t('settings.runtime.unreachableDetail')}
        onRetry={() => void refetch()}
      />
    )
  }
  return (
    <Card className="grid grid-cols-1 gap-x-8 gap-y-4 p-5 sm:grid-cols-2">
      <Row label={t('settings.runtime.service')} value={data.service} />
      <Row label={t('settings.runtime.version')} value={data.version} />
      <Row
        label={t('settings.runtime.vectorStore')}
        value={data.vector_store}
        online={data.vector_store_connected}
      />
      <Row label={t('settings.runtime.embeddingProvider')} value={data.embedding_provider} />
      <Row
        label={t('settings.runtime.embeddingModel')}
        value={data.embedding_model}
        online={data.embedding_model_loaded}
      />
      <Row label={t('settings.runtime.uptime')} value={formatUptime(data.uptime_seconds)} />
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

/** Session note plus a Sign-out action mirroring the Topbar. */
function SecurityPanel() {
  const { t } = useTranslation()
  const { logout } = useAuth()
  return (
    <Card className="flex items-center justify-between gap-4 p-5">
      <div>
        <p className="text-[13px] text-ink">{t('settings.security.session')}</p>
        <p className="mt-0.5 text-xs text-ink-muted">{t('settings.security.sessionNote')}</p>
      </div>
      <Button variant="secondary" onClick={logout}>
        <Lock className="h-5 w-5" />
        {t('settings.security.signOut')}
      </Button>
    </Card>
  )
}

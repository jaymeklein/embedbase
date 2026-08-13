import type { UseMutationResult } from '@tanstack/react-query'
import { AlertTriangle, SearchX, Telescope } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { CollectionStat, SearchMode, SearchRequest, SearchResponse } from '../../api/types'
import { Card, EmptyState, QueryError, Skeleton } from '../ui'
import { apiErrorMessage } from '../../i18n/apiError'
import { ResultCard } from './ResultCard'

/** Per-mode chip styling; the label and hint are resolved from `search.mode.*` /
 *  `search.modeHint.*` at render time. */
const MODE_CLS: Record<SearchMode, string> = {
  hybrid: 'border-accent/40 text-accent',
  semantic: 'border-border text-ink-muted',
  bm25: 'border-border text-ink-muted',
  semantic_only: 'border-warn/40 text-warn',
}

/** Results pane: drives off the search mutation across all of its states. */
export function SearchResults({
  mutation,
}: {
  mutation: UseMutationResult<SearchResponse, Error, SearchRequest>
}) {
  const { t } = useTranslation()
  if (mutation.isPending) {
    return (
      <div className="flex flex-col gap-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-28 rounded-card" />
        ))}
      </div>
    )
  }
  if (mutation.isError) {
    return <QueryError title={t('search.failed')} message={apiErrorMessage(mutation.error, t)} />
  }
  const res = mutation.data
  if (!res) {
    return (
      <EmptyState
        icon={<Telescope className="h-7 w-7" />}
        title={t('search.empty.title')}
        description={t('search.empty.description')}
      />
    )
  }
  const query = mutation.variables?.query ?? ''
  return (
    <div className="flex flex-col gap-4">
      <ResultsHeader res={res} />
      {res.under_delivered && <UnderDeliveredBanner topK={mutation.variables?.top_k} />}
      {res.results.length === 0 ? (
        <EmptyState
          icon={<SearchX className="h-7 w-7" />}
          title={t('search.noMatches.title')}
          description={t('search.noMatches.description')}
        />
      ) : (
        res.results.map((r) => <ResultCard key={r.chunk_id} result={r} query={query} />)
      )}
      <CollectionStatsPanel stats={res.collection_stats} />
    </div>
  )
}

/** Mode chip + millisecond timing line. */
function ResultsHeader({ res }: { res: SearchResponse }) {
  const { t } = useTranslation()
  const mode = res.search_mode
  const cls = MODE_CLS[mode] ?? MODE_CLS.semantic
  return (
    <div className="flex items-center justify-between gap-3">
      <span
        className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${cls}`}
        title={t(`search.modeHint.${mode}`)}
      >
        {t(`search.mode.${mode}`)}
      </span>
      <span className="font-mono text-xs text-ink-faint">
        {t('search.timing', {
          embed: res.query_embedding_ms,
          search: res.search_ms,
          total: res.total_ms,
        })}
      </span>
    </div>
  )
}

/** Warns that filters or available matches starved the requested `top_k`. */
function UnderDeliveredBanner({ topK }: { topK?: number }) {
  const { t } = useTranslation()
  return (
    <div className="flex items-start gap-2 rounded-card border border-warn/30 bg-warn/5 px-3.5 py-3">
      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-warn" />
      <p className="text-[13px] text-ink-muted">
        {topK
          ? t('search.underDelivered.withTopK', { topK })
          : t('search.underDelivered.noTopK')}
      </p>
    </div>
  )
}

/** Per-collection retrieved / returned / contributed breakdown. */
function CollectionStatsPanel({ stats }: { stats: Record<string, CollectionStat> }) {
  const { t } = useTranslation()
  const entries = Object.values(stats)
  if (entries.length === 0) return null
  return (
    <Card className="p-4">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-ink-faint">
        {t('search.breakdown.title')}
      </h3>
      <div className="flex flex-col gap-2">
        {entries.map((s) => (
          <div key={`${s.workspace_name}/${s.name}`} className="flex items-center justify-between gap-3 text-[13px]">
            <span className="min-w-0 truncate text-ink">
              {s.name}
              <span className="ml-1 text-ink-faint">· {s.workspace_name}</span>
            </span>
            <span className="shrink-0 font-mono text-xs text-ink-muted">
              {t('search.breakdown.stats', {
                retrieved: s.retrieved_before_filter,
                returned: s.returned_after_filter,
                contributed: s.contributed_to_top_k,
              })}
            </span>
          </div>
        ))}
      </div>
    </Card>
  )
}

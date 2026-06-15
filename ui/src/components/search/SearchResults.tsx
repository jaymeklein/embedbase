import type { UseMutationResult } from '@tanstack/react-query'
import { AlertTriangle, SearchX, Telescope } from 'lucide-react'
import type { CollectionStat, SearchMode, SearchRequest, SearchResponse } from '../../api/types'
import { Card, EmptyState, QueryError, Skeleton } from '../ui'
import { ResultCard } from './ResultCard'

const MODE: Record<SearchMode, { label: string; cls: string; hint: string }> = {
  hybrid: { label: 'Hybrid', cls: 'border-accent/40 text-accent', hint: 'BM25 + semantic' },
  semantic: { label: 'Semantic', cls: 'border-border text-ink-muted', hint: 'Vector similarity' },
  semantic_only: {
    label: 'Semantic only',
    cls: 'border-warn/40 text-warn',
    hint: 'BM25 index unavailable — fell back to semantic',
  },
}

/** Results pane: drives off the search mutation across all of its states. */
export function SearchResults({
  mutation,
}: {
  mutation: UseMutationResult<SearchResponse, Error, SearchRequest>
}) {
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
    return <QueryError title="Search failed" message={mutation.error.message} />
  }
  const res = mutation.data
  if (!res) {
    return (
      <EmptyState
        icon={<Telescope className="h-6 w-6" />}
        title="Search your collections"
        description="Pick one or more collections on the left, type a query, and run it."
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
          icon={<SearchX className="h-6 w-6" />}
          title="No matches"
          description="No chunks matched this query in the selected collections. Try broadening the query or relaxing filters."
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
  const mode = MODE[res.search_mode] ?? MODE.semantic
  return (
    <div className="flex items-center justify-between gap-3">
      <span
        className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${mode.cls}`}
        title={mode.hint}
      >
        {mode.label}
      </span>
      <span className="font-mono text-xs text-ink-faint">
        embed {res.query_embedding_ms}ms · search {res.search_ms}ms · total {res.total_ms}ms
      </span>
    </div>
  )
}

/** Warns that filters or available matches starved the requested `top_k`. */
function UnderDeliveredBanner({ topK }: { topK?: number }) {
  return (
    <div className="flex items-start gap-2 rounded-card border border-warn/30 bg-warn/5 px-3.5 py-3">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn" />
      <p className="text-[13px] text-ink-muted">
        Fewer results than requested{topK ? ` (top_k ${topK})` : ''}. A selective filter or a small
        candidate pool left some slots unfilled.
      </p>
    </div>
  )
}

/** Per-collection retrieved / returned / contributed breakdown. */
function CollectionStatsPanel({ stats }: { stats: Record<string, CollectionStat> }) {
  const entries = Object.values(stats)
  if (entries.length === 0) return null
  return (
    <Card className="p-4">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-ink-faint">
        Per-collection breakdown
      </h3>
      <div className="flex flex-col gap-2">
        {entries.map((s) => (
          <div key={`${s.workspace_name}/${s.name}`} className="flex items-center justify-between gap-3 text-[13px]">
            <span className="min-w-0 truncate text-ink">
              {s.name}
              <span className="ml-1 text-ink-faint">· {s.workspace_name}</span>
            </span>
            <span className="shrink-0 font-mono text-xs text-ink-muted">
              {s.retrieved_before_filter} → {s.returned_after_filter} · {s.contributed_to_top_k} in top-k
            </span>
          </div>
        ))}
      </div>
    </Card>
  )
}

import { useState } from 'react'
import { Search as SearchIcon } from 'lucide-react'
import { useSearch } from '../api/hooks'
import type { SearchFilters, SearchModeRequest } from '../api/types'
import { Button, Card, Field, Input } from '../components/ui'
import { WorkspaceTree } from '../components/search/WorkspaceTree'
import { SearchResults } from '../components/search/SearchResults'

const MODES: { value: SearchModeRequest; label: string }[] = [
  { value: 'hybrid', label: 'Hybrid' },
  { value: 'semantic', label: 'Semantic' },
  { value: 'bm25', label: 'BM25' },
]

interface FilterForm {
  language: string
  filename: string
  tags: string
}

const EMPTY_FILTERS: FilterForm = { language: '', filename: '', tags: '' }

/** Build a `SearchFilters` payload, or undefined when no filter is set. */
function buildFilters(f: FilterForm): SearchFilters | undefined {
  const language = f.language.trim() || undefined
  const filename = f.filename.trim() || undefined
  const tags = f.tags
    .split(',')
    .map((t) => t.trim())
    .filter(Boolean)
  if (!language && !filename && tags.length === 0) return undefined
  return { language, filename, tags: tags.length > 0 ? tags : undefined }
}

/** Two-pane hybrid search: pick collections, query, read ranked chunks. */
export default function Search() {
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [query, setQuery] = useState('')
  const [topK, setTopK] = useState(5)
  const [mode, setMode] = useState<SearchModeRequest>('hybrid')
  const [alpha, setAlpha] = useState(0.7)
  const [filters, setFilters] = useState<FilterForm>(EMPTY_FILTERS)
  const searchMut = useSearch()

  const toggle = (id: string) =>
    setSelected((s) => {
      const next = new Set(s)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  const toggleMany = (ids: string[], select: boolean) =>
    setSelected((s) => {
      const next = new Set(s)
      ids.forEach((id) => (select ? next.add(id) : next.delete(id)))
      return next
    })

  const canSearch = selected.size > 0 && query.trim().length > 0
  const run = () => {
    if (!canSearch) return
    searchMut.mutate({
      query: query.trim(),
      collection_ids: [...selected],
      top_k: topK,
      mode,
      hybrid_alpha: alpha,
      filters: buildFilters(filters),
    })
  }

  return (
    <div className="animate-fade-in space-y-6">
      <header>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Search</h1>
        <p className="mt-1 text-[13px] text-ink-muted">
          Hybrid BM25 + semantic search across your collections.
        </p>
      </header>

      <div className="flex flex-col gap-6 lg:flex-row">
        <aside className="lg:w-72 lg:shrink-0">
          <Card className="p-3">
            <h2 className="mb-2 px-1 text-xs font-semibold uppercase tracking-wide text-ink-faint">
              Collections {selected.size > 0 && <span className="text-accent">· {selected.size}</span>}
            </h2>
            <WorkspaceTree selected={selected} onToggle={toggle} onToggleMany={toggleMany} />
          </Card>
        </aside>

        <div className="min-w-0 flex-1 space-y-4">
          <div className="flex gap-2">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') run()
              }}
              placeholder="Search your collections…"
            />
            <Button onClick={run} loading={searchMut.isPending} disabled={!canSearch}>
              <SearchIcon className="h-5 w-5" />
              Search
            </Button>
          </div>
          {selected.size === 0 && (
            <p className="text-xs text-ink-faint">Select at least one collection to search.</p>
          )}

          <Controls
            topK={topK}
            setTopK={setTopK}
            mode={mode}
            setMode={setMode}
            alpha={alpha}
            setAlpha={setAlpha}
            filters={filters}
            setFilters={setFilters}
          />

          <SearchResults mutation={searchMut} />
        </div>
      </div>
    </div>
  )
}

/** Query parameters: top_k, search mode, alpha, and optional metadata filters. */
function Controls({
  topK,
  setTopK,
  mode,
  setMode,
  alpha,
  setAlpha,
  filters,
  setFilters,
}: {
  topK: number
  setTopK: (n: number) => void
  mode: SearchModeRequest
  setMode: (m: SearchModeRequest) => void
  alpha: number
  setAlpha: (n: number) => void
  filters: FilterForm
  setFilters: (f: FilterForm) => void
}) {
  const [showFilters, setShowFilters] = useState(false)
  return (
    <Card className="flex flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <label className="flex items-center gap-2 text-[13px]">
          <span className="text-ink-muted">Top-K</span>
          <input
            type="range"
            min={1}
            max={20}
            value={topK}
            onChange={(e) => setTopK(Number(e.target.value))}
            className="accent-accent"
          />
          <span className="w-6 font-mono text-xs text-ink">{topK}</span>
        </label>
        <div className="flex items-center gap-2 text-[13px]">
          <span className="text-ink-muted">Mode</span>
          <div className="inline-flex overflow-hidden rounded-md border border-border" role="group">
            {MODES.map((m) => (
              <button
                key={m.value}
                type="button"
                onClick={() => setMode(m.value)}
                aria-pressed={mode === m.value}
                className={
                  'px-3 py-1 text-xs font-medium transition-colors ' +
                  (mode === m.value
                    ? 'bg-accent text-white'
                    : 'bg-transparent text-ink-muted hover:bg-canvas')
                }
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>
        {mode === 'hybrid' && (
          <label className="flex items-center gap-2 text-[13px]" title="0 = BM25 only, 1 = semantic only">
            <span className="text-ink-muted">Alpha</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={alpha}
              onChange={(e) => setAlpha(Number(e.target.value))}
              className="accent-accent"
            />
            <span className="w-8 font-mono text-xs text-ink">{alpha.toFixed(2)}</span>
          </label>
        )}
        <button
          type="button"
          onClick={() => setShowFilters((s) => !s)}
          className="text-xs font-medium text-accent hover:underline"
        >
          {showFilters ? 'Hide filters' : 'Filters'}
        </button>
      </div>
      {showFilters && (
        <div className="grid grid-cols-1 gap-3 border-t border-border pt-3 sm:grid-cols-3">
          <Field label="Language" htmlFor="f-lang">
            <Input
              id="f-lang"
              value={filters.language}
              onChange={(e) => setFilters({ ...filters, language: e.target.value })}
              placeholder="e.g. en"
            />
          </Field>
          <Field label="Filename" htmlFor="f-file">
            <Input
              id="f-file"
              value={filters.filename}
              onChange={(e) => setFilters({ ...filters, filename: e.target.value })}
              placeholder="e.g. report.pdf"
            />
          </Field>
          <Field label="Tags" htmlFor="f-tags" hint="Comma-separated">
            <Input
              id="f-tags"
              value={filters.tags}
              onChange={(e) => setFilters({ ...filters, tags: e.target.value })}
              placeholder="e.g. finance, q3"
            />
          </Field>
        </div>
      )}
    </Card>
  )
}

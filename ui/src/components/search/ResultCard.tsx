import type { ReactNode } from 'react'
import { FileText, Layers } from 'lucide-react'
import type { SearchResult } from '../../api/types'
import { Badge, Card } from '../ui'

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** Wrap occurrences of the query's words in <mark> for light emphasis. */
function highlight(text: string, query: string): ReactNode {
  const words = query
    .trim()
    .split(/\s+/)
    .filter((w) => w.length > 1)
    .map(escapeRegExp)
  if (words.length === 0) return text
  const pattern = words.join('|')
  const splitter = new RegExp(`(${pattern})`, 'gi')
  const matcher = new RegExp(`^(?:${pattern})$`, 'i')
  return text
    .split(splitter)
    .map((part, i) =>
      matcher.test(part) ? (
        <mark key={i} className="rounded bg-accent-weak px-0.5 text-ink">
          {part}
        </mark>
      ) : (
        part
      ),
    )
}

/** A single ranked search hit: rank, score, provenance, and emphasized text. */
export function ResultCard({ result, query }: { result: SearchResult; query: string }) {
  const src = result.source
  return (
    <Card className="p-4">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="font-mono text-xs text-ink-faint">#{result.rank}</span>
          {src && (
            <Badge>
              <Layers className="h-3 w-3" />
              {src.collection_name}
            </Badge>
          )}
          {src?.workspace_name && (
            <span className="truncate text-xs text-ink-faint">{src.workspace_name}</span>
          )}
        </div>
        <span className="shrink-0 font-mono text-xs text-accent" title="Relevance score">
          {result.score.toFixed(3)}
        </span>
      </div>
      {src && (src.filename || src.page_number !== null) && (
        <p className="mb-2 flex items-center gap-1.5 text-xs text-ink-muted">
          <FileText className="h-3 w-3 shrink-0" />
          <span className="truncate">{src.filename ?? 'Unknown file'}</span>
          {src.page_number !== null && <span className="text-ink-faint">· p.{src.page_number}</span>}
        </p>
      )}
      <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-ink-muted">
        {highlight(result.text, query)}
      </p>
    </Card>
  )
}

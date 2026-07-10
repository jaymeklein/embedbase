import { Button } from './ui'

/** Offset pager: "showing X–Y of N" + Prev/Next, bounded to the available pages. Shared by the
 *  documents listing and the ingestion queue. Renders nothing when there is nothing to page. */
export function Pager({
  page,
  pageSize,
  total,
  onPage,
  loading,
}: {
  page: number
  pageSize: number
  total: number
  onPage: (p: number) => void
  loading: boolean
}) {
  if (total === 0) return null
  const start = page * pageSize + 1
  const end = Math.min((page + 1) * pageSize, total)
  const lastPage = Math.max(0, Math.ceil(total / pageSize) - 1)
  return (
    <div className="flex items-center justify-between text-[13px] text-ink-muted">
      <span>
        Showing{' '}
        <span className="tabular-nums text-ink">
          {start}–{end}
        </span>{' '}
        of <span className="tabular-nums text-ink">{total}</span>
      </span>
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="sm"
          disabled={page === 0 || loading}
          onClick={() => onPage(page - 1)}
        >
          Previous
        </Button>
        <span className="tabular-nums">
          Page {page + 1} of {lastPage + 1}
        </span>
        <Button
          variant="ghost"
          size="sm"
          disabled={page >= lastPage || loading}
          onClick={() => onPage(page + 1)}
        >
          Next
        </Button>
      </div>
    </div>
  )
}

import { useTranslation } from 'react-i18next'
import { cn } from '../lib/cn'
import { Button } from './ui'

/** Offset pager: "showing X–Y of N" + Prev/Next, bounded to the available pages. Shared by the
 *  documents listing and the ingestion queue. Renders nothing when there is nothing to page —
 *  which is also what keeps a `sticky` pager from leaving an empty bar on an empty listing. */
export function Pager({
  page,
  pageSize,
  total,
  onPage,
  loading,
  sticky = false,
}: {
  page: number
  pageSize: number
  total: number
  onPage: (p: number) => void
  loading: boolean
  /** Pin to the bottom of the scroll area, so paging a long listing never means scrolling to the
   *  end of it first. Assumes the page gutter both callers share (see the class note below). */
  sticky?: boolean
}) {
  const { t } = useTranslation()
  if (total === 0) return null
  const start = page * pageSize + 1
  const end = Math.min((page + 1) * pageSize, total)
  const lastPage = Math.max(0, Math.ceil(total / pageSize) - 1)
  return (
    <div
      className={cn(
        'flex items-center justify-between text-[13px] text-ink-muted',
        // -mx-8/px-8 span the page's px-8 gutter so rows can't show beside the bar, and the
        // background is opaque so they can't show through it.
        sticky && 'sticky bottom-0 z-10 -mx-8 border-t border-border bg-canvas px-8 py-3',
      )}
    >
      <span className="tabular-nums">{t('ui.pager.range', { from: start, to: end, total })}</span>
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="sm"
          disabled={page === 0 || loading}
          onClick={() => onPage(page - 1)}
        >
          {t('ui.pager.previous')}
        </Button>
        <span className="tabular-nums">{t('ui.pager.page', { page: page + 1, pages: lastPage + 1 })}</span>
        <Button
          variant="ghost"
          size="sm"
          disabled={page >= lastPage || loading}
          onClick={() => onPage(page + 1)}
        >
          {t('ui.pager.next')}
        </Button>
      </div>
    </div>
  )
}

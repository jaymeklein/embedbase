import { useTranslation } from 'react-i18next'
import { cn } from '../../lib/cn'

export type DocStatus = 'uploading' | 'pending' | 'processing' | 'done' | 'failed' | 'deleting'

/** Document states plus `rate_limited` — a job-only pause the ingestion queue surfaces. */
export type BadgeStatus = DocStatus | 'rate_limited'

// Presentation only — the human label is resolved from `status.<token>` at render
// so it follows the active language (a module-const label never would).
const STYLE: Record<BadgeStatus, { dot: string; text: string }> = {
  uploading: { dot: 'bg-accent animate-pulse', text: 'text-accent' },
  pending: { dot: 'bg-pending', text: 'text-ink-muted' },
  processing: { dot: 'bg-warn animate-pulse', text: 'text-warn' },
  done: { dot: 'bg-ok', text: 'text-ok' },
  failed: { dot: 'bg-err', text: 'text-err' },
  deleting: { dot: 'bg-warn animate-pulse', text: 'text-warn' },
  rate_limited: { dot: 'bg-warn animate-pulse', text: 'text-warn' },
}

/** Ingestion status pill with a semantic color dot. */
export function StatusBadge({ status }: { status: BadgeStatus }) {
  const { t } = useTranslation()
  const safe: BadgeStatus = status in STYLE ? status : 'pending'
  const s = STYLE[safe]
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-2 py-0.5 text-xs font-medium">
      <span className={cn('h-1.5 w-1.5 rounded-full', s.dot)} />
      <span className={s.text}>{t(`status.${safe}`)}</span>
    </span>
  )
}

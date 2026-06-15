import { cn } from '../../lib/cn'

export type DocStatus = 'pending' | 'processing' | 'done' | 'failed' | 'deleting'

const MAP: Record<DocStatus, { label: string; dot: string; text: string }> = {
  pending: { label: 'Pending', dot: 'bg-pending', text: 'text-ink-muted' },
  processing: { label: 'Processing', dot: 'bg-warn animate-pulse', text: 'text-warn' },
  done: { label: 'Done', dot: 'bg-ok', text: 'text-ok' },
  failed: { label: 'Failed', dot: 'bg-err', text: 'text-err' },
  deleting: { label: 'Deleting', dot: 'bg-warn animate-pulse', text: 'text-warn' },
}

/** Ingestion status pill with a semantic color dot. */
export function StatusBadge({ status }: { status: DocStatus }) {
  const s = MAP[status] ?? MAP.pending
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-2 py-0.5 text-xs font-medium">
      <span className={cn('h-1.5 w-1.5 rounded-full', s.dot)} />
      <span className={s.text}>{s.label}</span>
    </span>
  )
}

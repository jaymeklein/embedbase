import { ShieldAlert } from 'lucide-react'
import { Button, CopyButton } from '../ui'

/**
 * One-time reveal of a freshly generated secret (an API key or a temp password),
 * with copy + a no-recovery warning. Shown once and never retrievable again, so
 * never persist it.
 */
export function RevealOncePanel({
  secret,
  message,
  onDone,
}: {
  secret: string
  message: string
  onDone: () => void
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start gap-2 rounded-control border border-warn/30 bg-warn/5 px-3 py-2.5">
        <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-warn" />
        <p className="text-[13px] text-ink-muted">{message}</p>
      </div>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded-control border border-border bg-canvas px-3 py-2 font-mono text-[13px] text-ink">
          {secret}
        </code>
        <CopyButton text={secret} label="Copy" size="md" iconClassName="h-5 w-5" className="shrink-0" />
      </div>
      <div className="flex justify-end">
        <Button onClick={onDone}>Done</Button>
      </div>
    </div>
  )
}

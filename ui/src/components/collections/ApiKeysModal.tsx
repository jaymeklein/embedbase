import { useEffect, useState } from 'react'
import { Check, Copy, KeyRound, ShieldAlert, Trash2 } from 'lucide-react'
import { useApiKeys, useMintApiKey, useRevokeApiKey } from '../../api/hooks'
import type { ApiKey, MintedApiKey } from '../../api/types'
import { Button, EmptyState, Input, Modal, QueryError, Skeleton, useToast } from '../ui'
import { formatDate, timeAgo } from '../../lib/format'

/** Best-effort clipboard copy with a graceful fallback toast. */
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

/**
 * Per-collection API key manager: list existing keys, mint a new one (shown in
 * full exactly once), and revoke. Used as a modal launched from a collection.
 */
export function ApiKeysModal({
  open,
  wsId,
  colId,
  collectionName,
  onClose,
}: {
  open: boolean
  wsId: string
  colId: string
  collectionName: string
  onClose: () => void
}) {
  const { data, isLoading, isError, error, refetch } = useApiKeys(wsId, colId)
  const mintMut = useMintApiKey(wsId, colId)
  const [label, setLabel] = useState('')
  const [minted, setMinted] = useState<MintedApiKey | null>(null)
  const toast = useToast()

  // Forget any one-time secret and the draft label whenever the modal closes.
  useEffect(() => {
    if (!open) {
      setMinted(null)
      setLabel('')
    }
  }, [open])

  const mint = () => {
    mintMut.mutate(
      { label: label.trim() || undefined },
      {
        onSuccess: (key) => {
          setMinted(key)
          setLabel('')
        },
        onError: (e) => toast.error(e.message),
      },
    )
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`API keys — ${collectionName}`}
      className="max-w-lg"
    >
      {minted ? (
        <MintedKeyPanel minted={minted} onDone={() => setMinted(null)} />
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex items-end gap-2">
            <Input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') mint()
              }}
              placeholder="Label (optional), e.g. CI pipeline"
              aria-label="New key label"
            />
            <Button onClick={mint} loading={mintMut.isPending}>
              Create key
            </Button>
          </div>
          <KeyList
            data={data}
            isLoading={isLoading}
            isError={isError}
            message={error?.message}
            onRetry={() => void refetch()}
            wsId={wsId}
            colId={colId}
          />
        </div>
      )}
    </Modal>
  )
}

/** One-time reveal of a freshly minted key, with copy + a no-recovery warning. */
function MintedKeyPanel({ minted, onDone }: { minted: MintedApiKey; onDone: () => void }) {
  const [copied, setCopied] = useState(false)
  const toast = useToast()
  const copy = async () => {
    if (await copyText(minted.raw_key)) {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } else {
      toast.error('Copy failed — select the key and copy it manually.')
    }
  }
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start gap-2 rounded-control border border-warn/30 bg-warn/5 px-3 py-2.5">
        <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-warn" />
        <p className="text-[13px] text-ink-muted">
          Copy this key now — it is shown in full only once and cannot be retrieved again.
        </p>
      </div>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded-control border border-border bg-canvas px-3 py-2 font-mono text-[13px] text-ink">
          {minted.raw_key}
        </code>
        <Button variant="secondary" onClick={copy} aria-label="Copy key">
          {copied ? <Check className="h-5 w-5 text-ok" /> : <Copy className="h-5 w-5" />}
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
      <div className="flex justify-end">
        <Button onClick={onDone}>Done</Button>
      </div>
    </div>
  )
}

/** Render the key list across its loading / error / empty / data states. */
function KeyList({
  data,
  isLoading,
  isError,
  message,
  onRetry,
  wsId,
  colId,
}: {
  data: ApiKey[] | undefined
  isLoading: boolean
  isError: boolean
  message?: string
  onRetry: () => void
  wsId: string
  colId: string
}) {
  if (isLoading) {
    return (
      <div className="flex flex-col gap-2">
        {[0, 1].map((i) => (
          <Skeleton key={i} className="h-14 rounded-card" />
        ))}
      </div>
    )
  }
  if (isError) {
    return <QueryError title="Could not load keys" message={message} onRetry={onRetry} />
  }
  if (!data || data.length === 0) {
    return (
      <EmptyState
        icon={<KeyRound className="h-7 w-7" />}
        title="No API keys"
        description="Mint a key above to let a client query this collection."
      />
    )
  }
  return (
    <div className="divide-y divide-border rounded-card border border-border">
      {data.map((key) => (
        <KeyRow key={key.id} apiKey={key} wsId={wsId} colId={colId} />
      ))}
    </div>
  )
}

/** A single key row: metadata plus an inline-confirmed revoke. */
function KeyRow({ apiKey, wsId, colId }: { apiKey: ApiKey; wsId: string; colId: string }) {
  const revokeMut = useRevokeApiKey(wsId, colId)
  const [confirming, setConfirming] = useState(false)
  const toast = useToast()
  const revoke = () => {
    revokeMut.mutate(apiKey.id, {
      onSuccess: () => toast.success('Key revoked.'),
      onError: (e) => toast.error(e.message),
    })
  }
  return (
    <div className="flex items-center justify-between gap-3 px-3.5 py-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <code className="font-mono text-[13px] text-ink">{apiKey.key_prefix}…</code>
          <span className="truncate text-xs text-ink-muted">{apiKey.label || 'Unlabelled'}</span>
        </div>
        <p className="mt-0.5 text-xs text-ink-faint">
          Created {formatDate(apiKey.created_at)} ·{' '}
          {apiKey.last_used_at ? `used ${timeAgo(apiKey.last_used_at)}` : 'never used'}
        </p>
      </div>
      {confirming ? (
        <div className="flex shrink-0 items-center gap-1.5">
          <Button variant="danger" size="sm" onClick={revoke} loading={revokeMut.isPending}>
            Revoke
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setConfirming(false)}
            disabled={revokeMut.isPending}
          >
            Cancel
          </Button>
        </div>
      ) : (
        <Button
          variant="ghost"
          size="sm"
          aria-label={`Revoke key ${apiKey.key_prefix}`}
          onClick={() => setConfirming(true)}
          className="h-7 w-7 shrink-0 px-0 hover:text-err"
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      )}
    </div>
  )
}

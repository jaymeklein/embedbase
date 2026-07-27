import { useEffect, useState } from 'react'
import { KeyRound, RefreshCw, Trash2 } from 'lucide-react'
import { useMintUserKey, useRevokeUserKey } from '../../api/hooks'
import type { MintedUserKey, User } from '../../api/types'
import { Button, Modal, useToast } from '../ui'
import { formatDate, timeAgo } from '../../lib/format'
import { RevealOncePanel } from './RevealOncePanel'

/**
 * The user's single API key: create it, rotate it (mint replaces the old one),
 * reveal the raw secret exactly once, or revoke it. Launched as a modal per user.
 */
export function UserKeyModal({
  open,
  user,
  onClose,
}: {
  open: boolean
  user: User | null
  onClose: () => void
}) {
  const mintMut = useMintUserKey()
  const revokeMut = useRevokeUserKey()
  const [minted, setMinted] = useState<MintedUserKey | null>(null)
  const [confirmingRevoke, setConfirmingRevoke] = useState(false)
  // The `user` prop is a frozen snapshot; reflect a just-revoked key locally so the
  // dialog updates immediately instead of showing the stale key until it's reopened.
  const [revoked, setRevoked] = useState(false)
  const toast = useToast()

  // Forget any one-time secret + confirm/revoke state whenever the modal closes.
  useEffect(() => {
    if (!open) {
      setMinted(null)
      setConfirmingRevoke(false)
      setRevoked(false)
    }
  }, [open])

  if (!user) return null
  const existing = revoked ? null : user.api_key

  const mint = () =>
    mintMut.mutate(
      { id: user.id, body: {} },
      { onSuccess: (key) => setMinted(key), onError: (e) => toast.error(e.message) },
    )

  const revoke = () =>
    revokeMut.mutate(user.id, {
      onSuccess: () => {
        toast.success('Key revoked.')
        setConfirmingRevoke(false)
        setRevoked(true)
      },
      onError: (e) => toast.error(e.message),
    })

  return (
    <Modal open={open} onClose={onClose} title={`API key — ${user.email ?? user.username}`} className="max-w-lg">
      {minted ? (
        // Close on Done rather than returning to the management view: the `user`
        // prop is a frozen snapshot, so its key metadata is stale post-rotation.
        // Reopening reads the freshly-invalidated list.
        <RevealOncePanel
          secret={minted.raw_key}
          message="Copy this key now — it is shown in full only once and cannot be retrieved again."
          onDone={onClose}
        />
      ) : (
        <div className="flex flex-col gap-4">
          {existing ? (
            <div className="flex items-center justify-between gap-3 rounded-card border border-border px-3.5 py-3">
              <div className="min-w-0">
                <code className="font-mono text-[13px] text-ink">{existing.key_prefix}…</code>
                <p className="mt-0.5 text-xs text-ink-faint">
                  Created {formatDate(existing.created_at)} ·{' '}
                  {existing.last_used_at ? `used ${timeAgo(existing.last_used_at)}` : 'never used'}
                </p>
              </div>
              {confirmingRevoke ? (
                <div className="flex shrink-0 items-center gap-1.5">
                  <Button variant="danger" size="sm" onClick={revoke} loading={revokeMut.isPending}>
                    Revoke
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setConfirmingRevoke(false)}
                    disabled={revokeMut.isPending}
                  >
                    Cancel
                  </Button>
                </div>
              ) : (
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label="Revoke key"
                  onClick={() => setConfirmingRevoke(true)}
                  className="h-10 w-10 shrink-0 px-0 hover:text-err"
                >
                  <Trash2 className="h-7 w-7" />
                </Button>
              )}
            </div>
          ) : (
            <p className="text-[13px] text-ink-muted">This user has no API key yet.</p>
          )}
          <Button onClick={mint} loading={mintMut.isPending}>
            {existing ? (
              <>
                <RefreshCw className="h-5 w-5" />
                Rotate key
              </>
            ) : (
              <>
                <KeyRound className="h-5 w-5" />
                Create key
              </>
            )}
          </Button>
          {existing && (
            <p className="text-xs text-ink-faint">
              Rotating replaces the current key — the old one stops working immediately.
            </p>
          )}
        </div>
      )}
    </Modal>
  )
}

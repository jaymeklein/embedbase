import { useEffect, useState } from 'react'
import { KeyRound, RefreshCw, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useMintUserKey, useRevokeUserKey } from '../../api/hooks'
import type { MintedUserKey, User } from '../../api/types'
import { Button, Modal, useToast } from '../ui'
import { apiErrorMessage } from '../../i18n/apiError'
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
  const { t } = useTranslation()
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
      { onSuccess: (key) => setMinted(key), onError: (e) => toast.error(apiErrorMessage(e, t)) },
    )

  const revoke = () =>
    revokeMut.mutate(user.id, {
      onSuccess: () => {
        toast.success(t('users.key.toast.revoked'))
        setConfirmingRevoke(false)
        setRevoked(true)
      },
      onError: (e) => toast.error(apiErrorMessage(e, t)),
    })

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('users.key.title', { name: user.email ?? user.username })}
      className="max-w-lg"
    >
      {minted ? (
        // Close on Done rather than returning to the management view: the `user`
        // prop is a frozen snapshot, so its key metadata is stale post-rotation.
        // Reopening reads the freshly-invalidated list.
        <RevealOncePanel
          secret={minted.raw_key}
          message={t('users.key.revealMessage')}
          onDone={onClose}
        />
      ) : (
        <div className="flex flex-col gap-4">
          {existing ? (
            <div className="flex items-center justify-between gap-3 rounded-card border border-border px-3.5 py-3">
              <div className="min-w-0">
                <code className="font-mono text-[13px] text-ink">{existing.key_prefix}…</code>
                <p className="mt-0.5 text-xs text-ink-faint">
                  {t('common.created', { date: formatDate(existing.created_at) })} ·{' '}
                  {existing.last_used_at
                    ? t('users.key.used', { ago: timeAgo(existing.last_used_at) })
                    : t('users.key.neverUsed')}
                </p>
              </div>
              {confirmingRevoke ? (
                <div className="flex shrink-0 items-center gap-1.5">
                  <Button variant="danger" size="sm" onClick={revoke} loading={revokeMut.isPending}>
                    {t('users.revoke')}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setConfirmingRevoke(false)}
                    disabled={revokeMut.isPending}
                  >
                    {t('common.cancel')}
                  </Button>
                </div>
              ) : (
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={t('users.key.revokeAria')}
                  onClick={() => setConfirmingRevoke(true)}
                  className="h-10 w-10 shrink-0 px-0 hover:text-err"
                >
                  <Trash2 className="h-7 w-7" />
                </Button>
              )}
            </div>
          ) : (
            <p className="text-[13px] text-ink-muted">{t('users.key.none')}</p>
          )}
          <Button onClick={mint} loading={mintMut.isPending}>
            {existing ? (
              <>
                <RefreshCw className="h-5 w-5" />
                {t('users.key.rotate')}
              </>
            ) : (
              <>
                <KeyRound className="h-5 w-5" />
                {t('users.key.create')}
              </>
            )}
          </Button>
          {existing && <p className="text-xs text-ink-faint">{t('users.key.rotateHint')}</p>}
        </div>
      )}
    </Modal>
  )
}

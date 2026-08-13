import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../api/client'
import { apiErrorMessage } from '../i18n/apiError'
import { Button, Field, Input } from '../components/ui'
import { useAuth } from './AuthContext'

const MIN_LENGTH = 12

/**
 * Shared current/new/confirm password form. Calls `changePassword` (which stores a
 * fresh session token) and reports success. Used by the forced first-login screen
 * and the voluntary change modal.
 */
export function ChangePasswordForm({
  submitLabel,
  onSuccess,
}: {
  submitLabel?: string
  onSuccess?: () => void
}) {
  const { t } = useTranslation()
  const { changePassword } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const tooShort = next.length > 0 && next.length < MIN_LENGTH
  const mismatch = confirm.length > 0 && next !== confirm
  const canSubmit = Boolean(current) && next.length >= MIN_LENGTH && next === confirm && !busy

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!canSubmit) return
    setBusy(true)
    setError(null)
    try {
      await changePassword(current, next)
      onSuccess?.()
    } catch (err) {
      setError(err instanceof ApiError ? apiErrorMessage(err, t) : t('auth.password.failed'))
      setBusy(false)
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4">
      <Field label={t('auth.password.current')} htmlFor="cp-current">
        <Input
          id="cp-current"
          type="password"
          autoComplete="current-password"
          autoFocus
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
        />
      </Field>
      <Field
        label={t('auth.password.new')}
        htmlFor="cp-new"
        hint={t('auth.password.hint', { min: MIN_LENGTH })}
        error={tooShort ? t('auth.password.tooShort', { min: MIN_LENGTH }) : undefined}
      >
        <Input
          id="cp-new"
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
        />
      </Field>
      <Field
        label={t('auth.password.confirm')}
        htmlFor="cp-confirm"
        error={mismatch ? t('auth.password.mismatch') : undefined}
      >
        <Input
          id="cp-confirm"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
      </Field>
      {error && (
        <p className="text-xs text-err" role="alert">
          {error}
        </p>
      )}
      <Button type="submit" loading={busy} disabled={!canSubmit} className="w-full">
        {busy ? t('common.saving') : (submitLabel ?? t('auth.password.update'))}
      </Button>
    </form>
  )
}

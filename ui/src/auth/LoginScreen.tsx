import { useEffect, useRef, useState, type FormEvent } from 'react'
import { LogIn } from 'lucide-react'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../api/client'
import { apiErrorMessage } from '../i18n/apiError'
import { Button } from '../components/ui/Button'
import { Input } from '../components/ui/Input'
import { useAuth } from './AuthContext'
import { ApiReachLine, UnlockScreen, useApiReach } from './UnlockScreen'

/** Maps a login failure to a coarse, credential-safe message. */
function loginError(err: unknown, t: TFunction): string {
  if (err instanceof ApiError && err.status === 401) return t('auth.login.invalid')
  return apiErrorMessage(err, t)
}

/**
 * Primary sign-in: username + password. Offers a "use master key" toggle that
 * swaps in the bootstrap {@link UnlockScreen} (break-glass admin access).
 */
export function LoginScreen() {
  const { t } = useTranslation()
  const { login } = useAuth()
  const { reach, health } = useApiReach()
  const [useMaster, setUseMaster] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  if (useMaster) return <UnlockScreen onBack={() => setUseMaster(false)} />

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!username.trim() || !password || busy) return
    setBusy(true)
    setError(null)
    try {
      await login(username.trim(), password)
    } catch (err) {
      setError(loginError(err, t))
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-4">
      <div className="w-full max-w-sm animate-fade-in">
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-card bg-accent-weak text-accent">
            <LogIn className="h-6 w-6" />
          </div>
          <h1 className="text-lg font-semibold tracking-tight text-ink">EmbedBase</h1>
          <p className="mt-1 text-[13px] text-ink-muted">{t('auth.login.subtitle')}</p>
        </div>

        <form onSubmit={onSubmit} className="flex flex-col gap-3">
          <Input
            ref={inputRef}
            autoComplete="username"
            placeholder={t('auth.field.username')}
            aria-label={t('auth.field.username')}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <Input
            type="password"
            autoComplete="current-password"
            placeholder={t('auth.field.password')}
            aria-label={t('auth.field.password')}
            aria-invalid={error != null}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {error && (
            <p className="text-xs text-err" role="alert">
              {error}
            </p>
          )}
          <Button
            type="submit"
            loading={busy}
            disabled={!username.trim() || !password}
            className="w-full"
          >
            {busy ? t('auth.login.submitting') : t('auth.login.submit')}
          </Button>
        </form>

        <button
          type="button"
          onClick={() => setUseMaster(true)}
          className="mt-4 w-full text-center text-xs text-ink-faint hover:text-ink-muted"
        >
          {t('auth.login.useMaster')}
        </button>

        <ApiReachLine reach={reach} health={health} />
      </div>
    </div>
  )
}

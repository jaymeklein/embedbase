import { useEffect, useRef, useState, type FormEvent } from 'react'
import { LogIn } from 'lucide-react'
import { ApiError } from '../api/client'
import { Button } from '../components/ui/Button'
import { Input } from '../components/ui/Input'
import { useAuth } from './AuthContext'
import { ApiReachLine, UnlockScreen, useApiReach } from './UnlockScreen'

/** Maps a login failure to a coarse, credential-safe message. */
function loginError(err: unknown): string {
  if (err instanceof ApiError) {
    return err.status === 401 ? 'Invalid username or password.' : err.message
  }
  return 'Could not reach the API. Is the stack running?'
}

/**
 * Primary sign-in: username + password. Offers a "use master key" toggle that
 * swaps in the bootstrap {@link UnlockScreen} (break-glass admin access).
 */
export function LoginScreen() {
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
      setError(loginError(err))
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
          <p className="mt-1 text-[13px] text-ink-muted">Sign in to your account.</p>
        </div>

        <form onSubmit={onSubmit} className="flex flex-col gap-3">
          <Input
            ref={inputRef}
            autoComplete="username"
            placeholder="Username"
            aria-label="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <Input
            type="password"
            autoComplete="current-password"
            placeholder="Password"
            aria-label="Password"
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
            {busy ? 'Signing in…' : 'Sign in'}
          </Button>
        </form>

        <button
          type="button"
          onClick={() => setUseMaster(true)}
          className="mt-4 w-full text-center text-xs text-ink-faint hover:text-ink-muted"
        >
          Use master key instead
        </button>

        <ApiReachLine reach={reach} health={health} />
      </div>
    </div>
  )
}

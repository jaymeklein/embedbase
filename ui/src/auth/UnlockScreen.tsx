import { useEffect, useRef, useState, type FormEvent } from 'react'
import { KeyRound, Loader2, ShieldCheck, ShieldAlert } from 'lucide-react'
import { api, ApiError } from '../api/client'
import type { Health } from '../api/types'
import { useAuth } from './AuthContext'
import { Button } from '../components/ui/Button'
import { Input } from '../components/ui/Input'

type ApiReach = 'checking' | 'up' | 'down'

/** Probe the public `/healthz` so the operator sees the stack is reachable. */
function useApiReach(): { reach: ApiReach; health: Health | null } {
  const [reach, setReach] = useState<ApiReach>('checking')
  const [health, setHealth] = useState<Health | null>(null)
  useEffect(() => {
    let active = true
    api
      .healthz()
      .then((h) => active && (setHealth(h), setReach('up')))
      .catch(() => active && setReach('down'))
    return () => {
      active = false
    }
  }, [])
  return { reach, health }
}

/** Maps an unlock failure to a key-safe message (never echoes the key). */
function unlockError(err: unknown): string {
  if (err instanceof ApiError) {
    return err.status === 401
      ? 'That key was rejected. Check it and try again.'
      : err.message
  }
  return 'Could not reach the API. Is the stack running?'
}

/**
 * Full-screen gate shown until a valid master key is held. The key is the only
 * credential — there is no login. Submitting verifies it against `/workspaces`.
 */
export function UnlockScreen() {
  const { unlock } = useAuth()
  const { reach, health } = useApiReach()
  const [key, setKey] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!key.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      await unlock(key.trim())
    } catch (err) {
      setError(unlockError(err))
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-4">
      <div className="w-full max-w-sm animate-fade-in">
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-card bg-accent-weak text-accent">
            <KeyRound className="h-6 w-6" />
          </div>
          <h1 className="text-lg font-semibold tracking-tight text-ink">EmbedBase</h1>
          <p className="mt-1 text-[13px] text-ink-muted">
            Enter your master key to unlock the console.
          </p>
        </div>

        <form onSubmit={onSubmit} className="flex flex-col gap-3">
          <Input
            ref={inputRef}
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder="Master key"
            aria-label="Master key"
            aria-invalid={error != null}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            className="font-mono"
          />
          {error && (
            <p className="text-xs text-err" role="alert">
              {error}
            </p>
          )}
          <Button type="submit" loading={busy} disabled={!key.trim()} className="w-full">
            {busy ? 'Verifying…' : 'Unlock'}
          </Button>
        </form>

        <ApiReachLine reach={reach} health={health} />
      </div>
    </div>
  )
}

/** Subtle API-reachability footer beneath the unlock form. */
function ApiReachLine({ reach, health }: { reach: ApiReach; health: Health | null }) {
  if (reach === 'checking') {
    return (
      <p className="mt-6 flex items-center justify-center gap-1.5 text-xs text-ink-faint">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        Checking API…
      </p>
    )
  }
  if (reach === 'down') {
    return (
      <p className="mt-6 flex items-center justify-center gap-1.5 text-xs text-err">
        <ShieldAlert className="h-3.5 w-3.5" />
        API unreachable
      </p>
    )
  }
  return (
    <p className="mt-6 flex items-center justify-center gap-1.5 text-xs text-ink-faint">
      <ShieldCheck className="h-3.5 w-3.5 text-ok" />
      API ready · {health?.embedding_model ?? 'model'}
    </p>
  )
}

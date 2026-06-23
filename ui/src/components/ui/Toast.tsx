import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import { AlertCircle, CheckCircle2, X } from 'lucide-react'
import { cn } from '../../lib/cn'

type ToastKind = 'success' | 'error'

interface ToastItem {
  id: number
  kind: ToastKind
  message: string
}

interface ToastApi {
  success: (message: string) => void
  error: (message: string) => void
}

const ToastContext = createContext<ToastApi | null>(null)

/** Access the toast API. Must be rendered within a ToastProvider. */
export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within ToastProvider')
  return ctx
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])

  const remove = useCallback((id: number) => {
    setToasts((list) => list.filter((t) => t.id !== id))
  }, [])

  const push = useCallback(
    (kind: ToastKind, message: string) => {
      const id = Date.now() + Math.random()
      setToasts((list) => [...list, { id, kind, message }])
      window.setTimeout(() => remove(id), 4000)
    },
    [remove],
  )

  const api = useMemo<ToastApi>(
    () => ({
      success: (message) => push('success', message),
      error: (message) => push('error', message),
    }),
    [push],
  )

  return (
    <ToastContext.Provider value={api}>
      {children}
      {createPortal(
        <div className="fixed bottom-4 right-4 z-[60] flex w-80 flex-col gap-2">
          {toasts.map((t) => (
            <div
              key={t.id}
              className={cn(
                'flex animate-fade-in items-start gap-2 rounded-card border bg-surface px-3.5 py-3 shadow-overlay',
                t.kind === 'success' ? 'border-ok/30' : 'border-err/30',
              )}
            >
              {t.kind === 'success' ? (
                <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-ok" />
              ) : (
                <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-err" />
              )}
              <p className="flex-1 text-[13px] text-ink">{t.message}</p>
              <button
                onClick={() => remove(t.id)}
                className="text-ink-faint transition-colors hover:text-ink"
                aria-label="Dismiss"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  )
}

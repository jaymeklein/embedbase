import { Component, type ErrorInfo, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

/**
 * Fallback UI for a caught render error. Split out as a function component so it can
 * localize its labels via `useTranslation` — a hook the class boundary itself can't use.
 * The raw `error.message` is shown untranslated: it's the caught JS error's own text.
 */
function ErrorFallback({ error, onReset }: { error: Error; onReset: () => void }): ReactNode {
  const { t } = useTranslation()
  return (
    <div className="flex h-screen flex-col items-center justify-center gap-3 bg-canvas px-6 text-center">
      <h1 className="text-lg font-semibold text-ink">{t('ui.errorBoundary.title')}</h1>
      <p className="max-w-md text-[13px] text-ink-muted">{error.message}</p>
      <button
        onClick={onReset}
        className="rounded-control border border-border bg-surface px-4 py-2 text-sm font-medium text-ink transition-colors hover:bg-canvas"
      >
        {t('common.retry')}
      </button>
    </div>
  )
}

/** Catches render-time errors and shows a recoverable fallback. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surface to the console; structured logging arrives in Delivery 6.
    console.error('UI ErrorBoundary caught:', error, info.componentStack)
  }

  render(): ReactNode {
    const { error } = this.state
    if (error) {
      return <ErrorFallback error={error} onReset={() => this.setState({ error: null })} />
    }
    return this.props.children
  }
}

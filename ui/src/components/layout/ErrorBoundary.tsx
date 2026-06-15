import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
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
      return (
        <div className="flex h-screen flex-col items-center justify-center gap-3 bg-canvas px-6 text-center">
          <h1 className="text-lg font-semibold text-ink">Something went wrong</h1>
          <p className="max-w-md text-[13px] text-ink-muted">{error.message}</p>
          <button
            onClick={() => this.setState({ error: null })}
            className="rounded-control border border-border bg-surface px-4 py-2 text-sm font-medium text-ink transition-colors hover:bg-canvas"
          >
            Try again
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

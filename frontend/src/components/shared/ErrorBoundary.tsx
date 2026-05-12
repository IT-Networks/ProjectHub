import { Component, type ReactNode } from 'react'
import { AlertTriangle, RotateCcw, Home } from 'lucide-react'

interface Props {
  children: ReactNode
  fallback?: (error: Error, reset: () => void) => ReactNode
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary] Uncaught error:', error, info.componentStack)
  }

  reset = () => {
    this.setState({ error: null })
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    if (this.props.fallback) {
      return this.props.fallback(error, this.reset)
    }

    return (
      <div className="flex min-h-[50vh] items-center justify-center p-6">
        <div className="max-w-md rounded-lg border border-destructive/30 bg-destructive/5 p-6">
          <div className="mb-3 flex items-center gap-2 text-destructive">
            <AlertTriangle className="h-5 w-5" />
            <h2 className="text-base font-semibold">Etwas ist schiefgelaufen</h2>
          </div>
          <p className="mb-3 text-sm text-muted-foreground">
            Ein unerwarteter Fehler ist aufgetreten. Die Ansicht konnte nicht geladen werden.
          </p>
          <pre className="mb-4 max-h-32 overflow-auto rounded bg-muted/50 p-2 text-[11px] text-muted-foreground">
            {error.message}
          </pre>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={this.reset}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-sm hover:bg-accent"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              Erneut versuchen
            </button>
            <button
              type="button"
              onClick={() => { window.location.href = '/' }}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-sm hover:bg-accent"
            >
              <Home className="h-3.5 w-3.5" />
              Zum Dashboard
            </button>
          </div>
        </div>
      </div>
    )
  }
}

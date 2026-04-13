import { useLocation } from 'react-router-dom'
import { useOfflineStore } from '@/hooks/useOffline'
import { useSSEStore } from '@/hooks/useSSE'
import { useThemeStore } from '@/stores/themeStore'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const ROUTE_TITLES: Record<string, string> = {
  '/': 'Dashboard',
  '/projekte': 'Projekte',
  '/kanban': 'Kanban',
  '/timeline': 'Timeline',
  '/inbox': 'Inbox',
  '/queue': 'Todo-Queue',
  '/einstellungen': 'Einstellungen',
}

export function TopBar() {
  const location = useLocation()
  const title = ROUTE_TITLES[location.pathname] || 'ProjectHub'
  const aiConnected = useOfflineStore((s) => s.aiAssistConnected)
  const sseConnected = useSSEStore((s) => s.connected)
  const theme = useThemeStore((s) => s.theme)
  const toggleTheme = useThemeStore((s) => s.toggleTheme)

  return (
    <header className="flex h-14 items-center justify-between border-b border-border bg-background px-6">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold">{title}</h1>
      </div>

      <div className="flex items-center gap-3">
        {/* Global Search Trigger */}
        <button
          onClick={() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true }))}
          className="flex cursor-pointer items-center gap-2 rounded-md border border-input bg-muted/50 px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted"
        >
          <span>Suche...</span>
          <kbd className="rounded border border-border bg-background px-1.5 py-0.5 text-xs">
            Ctrl+K
          </kbd>
        </button>

        {/* Theme Toggle */}
        <Button
          variant="ghost"
          size="sm"
          onClick={toggleTheme}
          className="h-8 w-8 p-0"
          aria-label={theme === 'dark' ? 'Zum hellen Modus wechseln' : 'Zum dunklen Modus wechseln'}
        >
          {theme === 'dark' ? '☀' : '☾'}
        </Button>

        {/* SSE Connection */}
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className={cn('h-2 w-2 rounded-full', sseConnected ? 'bg-green-500' : 'bg-red-500')} />
          SSE
        </div>

        {/* AI-Assist Connection */}
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className={cn('h-2 w-2 rounded-full', aiConnected ? 'bg-green-500' : 'bg-yellow-500')} />
          AI-Assist
        </div>
      </div>
    </header>
  )
}

import { RefreshCw, CheckCircle2, AlertCircle, Circle } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { SyncRun } from '@/stores/projectSyncStore'

interface Props {
  projectId: string
  running: boolean
  lastRun: SyncRun | null
  pending: number
  onTrigger: () => void | Promise<unknown>
}

function formatRelative(iso: string): string {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const mins = Math.floor((Date.now() - d.getTime()) / 60000)
  if (mins < 1) return 'gerade eben'
  if (mins < 60) return `vor ${mins} min`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `vor ${hrs} h`
  return `vor ${Math.floor(hrs / 24)} d`
}

export function SyncIndicator({ running, lastRun, pending, onTrigger }: Props) {
  const tone =
    running ? 'brand'
    : lastRun?.status === 'error' ? 'danger'
    : lastRun?.status === 'partial' ? 'warning'
    : 'muted'

  const toneClass =
    tone === 'brand' ? 'text-brand'
    : tone === 'danger' ? 'text-red-500'
    : tone === 'warning' ? 'text-amber-500'
    : 'text-muted-foreground'

  const tooltip = running
    ? 'Sync läuft…'
    : lastRun?.status === 'error'
      ? `Letzter Sync fehlgeschlagen: ${lastRun.error_summary ?? 'unbekannt'}`
      : lastRun?.status === 'partial'
        ? `Teilweise erfolgreich (${lastRun.sources_failed} Fehler)`
        : lastRun
          ? `Zuletzt synchronisiert ${formatRelative(lastRun.started_at)}`
          : 'Noch nicht synchronisiert'

  const Icon = running ? RefreshCw
    : lastRun?.status === 'error' ? AlertCircle
    : lastRun?.status === 'ok' ? CheckCircle2
    : Circle

  return (
    <button
      type="button"
      onClick={() => onTrigger()}
      disabled={running}
      title={tooltip + (pending > 0 ? ` · ${pending} ausstehend` : '')}
      aria-label="Jetzt synchronisieren"
      className={cn(
        'relative flex h-8 items-center gap-1.5 rounded-md border border-input px-2 text-xs transition-colors',
        'hover:bg-accent hover:text-accent-foreground',
        'disabled:cursor-not-allowed disabled:opacity-70',
        toneClass,
      )}
    >
      <Icon className={cn('h-3.5 w-3.5', running && 'animate-spin')} />
      <span className="hidden sm:inline">
        {running ? 'Sync…' : 'Sync'}
      </span>
      {pending > 0 && !running && (
        <span className="ml-0.5 rounded-full bg-brand px-1.5 py-0 text-[10px] font-medium text-brand-foreground">
          {pending}
        </span>
      )}
    </button>
  )
}

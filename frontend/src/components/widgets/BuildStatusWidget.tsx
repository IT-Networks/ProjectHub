import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { useSSEEvent } from '@/hooks/useSSE'
import { cn } from '@/lib/utils'

interface BuildJob {
  job_name: string
  color: string
  last_build: { number: number; result: string; timestamp: number } | null
  path_name: string
}

const COLOR_MAP: Record<string, { bg: string; label: string }> = {
  blue: { bg: 'bg-green-500', label: 'Erfolg' },
  green: { bg: 'bg-green-500', label: 'Erfolg' },
  red: { bg: 'bg-red-500', label: 'Fehler' },
  yellow: { bg: 'bg-yellow-500', label: 'Instabil' },
  aborted: { bg: 'bg-gray-500', label: 'Abgebrochen' },
  disabled: { bg: 'bg-gray-400', label: 'Deaktiviert' },
  notbuilt: { bg: 'bg-gray-400', label: 'Nicht gebaut' },
  blue_anime: { bg: 'bg-green-500 animate-pulse', label: 'Läuft...' },
  red_anime: { bg: 'bg-red-500 animate-pulse', label: 'Läuft...' },
  yellow_anime: { bg: 'bg-yellow-500 animate-pulse', label: 'Läuft...' },
}

interface Props {
  config: Record<string, unknown>
}

export function BuildStatusWidget({ config }: Props) {
  const [builds, setBuilds] = useState<BuildJob[]>([])
  const [connected, setConnected] = useState(true)

  const load = async () => {
    try {
      const url = config.project_id ? `/builds/${config.project_id}` : '/builds'
      const data = await api.get<{ builds: BuildJob[]; connected: boolean }>(url)
      setBuilds(data.builds)
      setConnected(data.connected)
    } catch { /* offline */ }
  }

  useEffect(() => { load() }, [config.project_id])

  // SSE live update
  useSSEEvent('build_update', () => { load() })

  if (builds.length === 0) {
    return <p className="text-sm text-muted-foreground">Keine Jenkins-Jobs verknüpft</p>
  }

  return (
    <div className="space-y-2">
      {!connected && (
        <p className="text-xs text-yellow-400">(cached)</p>
      )}
      {builds.slice(0, 8).map((b) => {
        const c = COLOR_MAP[b.color] || COLOR_MAP.notbuilt
        return (
          <div key={`${b.path_name}:${b.job_name}`} className="flex items-center gap-2">
            <span className={cn('h-3 w-3 rounded-full', c.bg)} />
            <span className="flex-1 truncate text-sm">{b.job_name}</span>
            <span className="text-xs text-muted-foreground">
              {b.last_build ? `#${b.last_build.number}` : '-'}
            </span>
          </div>
        )
      })}
    </div>
  )
}

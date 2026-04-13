import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

interface ActivityItem {
  type: 'todo' | 'note' | 'research'
  action: string
  id: string
  title: string
  status?: string
  priority?: string
  project_id: string | null
  timestamp: string
}

const TYPE_ICONS: Record<string, string> = {
  todo: '☐',
  note: '📝',
  research: '🔍',
}

const TYPE_COLORS: Record<string, string> = {
  todo: 'border-l-blue-500',
  note: 'border-l-green-500',
  research: 'border-l-purple-500',
}

interface Props {
  config: Record<string, unknown>
}

export function ActivityWidget({ config }: Props) {
  const [activities, setActivities] = useState<ActivityItem[]>([])

  useEffect(() => {
    const load = async () => {
      try {
        const params = config.project_id ? `?project_id=${config.project_id}&limit=10` : '?limit=10'
        const data = await api.get<{ activities: ActivityItem[] }>(`/activity${params}`)
        setActivities(data.activities)
      } catch { /* offline */ }
    }
    load()
    const interval = setInterval(load, 60000)
    return () => clearInterval(interval)
  }, [config.project_id])

  if (activities.length === 0) {
    return <p className="text-sm text-muted-foreground">Keine Aktivitäten</p>
  }

  const formatTime = (ts: string) => {
    const d = new Date(ts)
    const now = new Date()
    const diff = now.getTime() - d.getTime()
    const mins = Math.floor(diff / 60000)
    if (mins < 1) return 'gerade eben'
    if (mins < 60) return `vor ${mins} Min.`
    const hours = Math.floor(mins / 60)
    if (hours < 24) return `vor ${hours} Std.`
    const days = Math.floor(hours / 24)
    if (days < 7) return `vor ${days} Tagen`
    return d.toLocaleDateString('de-DE')
  }

  return (
    <div className="space-y-1.5 max-h-[300px] overflow-y-auto">
      {activities.map((item) => (
        <div
          key={`${item.type}-${item.id}`}
          className={cn('flex items-start gap-2 border-l-2 pl-2 py-1', TYPE_COLORS[item.type])}
        >
          <span className="mt-0.5 text-xs">{TYPE_ICONS[item.type]}</span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-medium">{item.title}</p>
            <p className="text-xs text-muted-foreground">
              {item.action} — {formatTime(item.timestamp)}
            </p>
          </div>
        </div>
      ))}
    </div>
  )
}

import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { CATEGORY_LABELS, CATEGORY_COLORS } from '@/lib/types'
import type { KnowledgeStats, KnowledgeCategory } from '@/lib/types'

interface KnowledgeWidgetProps {
  config: Record<string, unknown>
}

export function KnowledgeWidget({ config }: KnowledgeWidgetProps) {
  const [stats, setStats] = useState<KnowledgeStats | null>(null)
  const projectId = config.project_id as string | undefined

  useEffect(() => {
    if (!projectId) return
    api.get<KnowledgeStats>(`/knowledge/${projectId}/stats`).then(setStats).catch(() => {})
  }, [projectId])

  if (!projectId) {
    return <p className="text-xs text-muted-foreground">Projekt wählen für Wissens-Widget</p>
  }

  if (!stats) {
    return <p className="text-xs text-muted-foreground">Laden...</p>
  }

  return (
    <div className="space-y-3">
      {/* Stats Row */}
      <div className="flex items-center gap-4 text-sm">
        <div>
          <span className="text-lg font-bold">{stats.total_items}</span>
          <span className="ml-1 text-xs text-muted-foreground">Einträge</span>
        </div>
        <div>
          <span className="text-lg font-bold">{stats.total_edges}</span>
          <span className="ml-1 text-xs text-muted-foreground">Verknüpfungen</span>
        </div>
      </div>

      {/* Category Distribution */}
      {Object.keys(stats.by_category).length > 0 && (
        <div className="flex flex-wrap gap-1">
          {Object.entries(stats.by_category).map(([cat, count]) => (
            <Badge
              key={cat}
              variant="outline"
              className="text-[10px]"
              style={{ borderColor: CATEGORY_COLORS[cat as KnowledgeCategory] }}
            >
              {CATEGORY_LABELS[cat as KnowledgeCategory]} ({count})
            </Badge>
          ))}
        </div>
      )}

      {/* Recent Items */}
      {stats.recent_items.length > 0 && (
        <div className="space-y-1">
          <p className="text-[10px] font-medium text-muted-foreground uppercase">Neueste</p>
          {stats.recent_items.slice(0, 3).map((item) => (
            <div key={item.id} className="flex items-center gap-2 text-xs">
              <span
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ backgroundColor: CATEGORY_COLORS[item.category as KnowledgeCategory] }}
              />
              <span className="truncate">{item.title}</span>
            </div>
          ))}
        </div>
      )}

      {stats.total_items === 0 && (
        <p className="text-xs text-muted-foreground">Noch kein Wissen erfasst</p>
      )}
    </div>
  )
}
